"""Configurable transfer rules: CRUD + materialise + evaluate (#86)."""
from __future__ import annotations

import json
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, ProfileDep, SessionDep
from app.domain import budget_periods, rbac, schedule, shared_contribution_approvals, transfer_rules
from app.domain.paydays import PaydayRule, RuleKind, ShiftPolicy, pay_date
from app.domain.recurring import advance
from app.persistence.models import (
    Account,
    AmountKind,
    AppMeta,
    Budget,
    Confidence,
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
    Frequency,
    Role,
    TransferRule,
    UserProfile,
)

router = APIRouter(prefix="/transfer-rules", tags=["transfer-rules"])


def _get(session: Session, household_id: int, rid: int) -> TransferRule:
    r = session.get(TransferRule, rid)
    if r is None or r.household_id != household_id:
        raise HTTPException(404, f"transfer rule {rid} not found")
    return r


def _retain_endpoints(session: Session, rule: TransferRule) -> None:
    """Freeze existing movement identity before changing its scheduling source."""
    session.execute(update(ExpectedMovement).where(
        ExpectedMovement.household_id == rule.household_id,
        ExpectedMovement.source_type == ExpectedSource.transfer_rule,
        ExpectedMovement.source_id == rule.id,
        ExpectedMovement.transfer_source_account_id.is_(None),
        ExpectedMovement.transfer_dest_account_id.is_(None),
    ).values(transfer_source_account_id=rule.source_account_id,
             transfer_dest_account_id=rule.dest_account_id))


def _check_accounts(
    session: Session, household_id: int,
    source_id: int, dest_id: int,
) -> None:
    for aid in (source_id, dest_id):
        a = session.get(Account, aid)
        if a is None or a.household_id != household_id:
            raise HTTPException(404, f"account {aid} not found")
    if source_id == dest_id:
        raise HTTPException(
            422, "source and destination accounts must differ")


@router.get("", response_model=schemas.Page[schemas.TransferRuleOut])
def list_rules(
    household: HouseholdDep, session: SessionDep,
    include_inactive: Annotated[bool, Query()] = False,
) -> schemas.Page[schemas.TransferRuleOut]:
    stmt = select(TransferRule).where(
        TransferRule.household_id == household.id)
    if not include_inactive:
        current_accounts = select(Account.id).where(
            Account.household_id == household.id, Account.archived.is_(False))
        stmt = stmt.where(TransferRule.active.is_(True),
                          TransferRule.source_account_id.in_(current_accounts),
                          TransferRule.dest_account_id.in_(current_accounts))
    rows = list(session.execute(stmt).scalars().all())
    return schemas.Page(
        items=[schemas.TransferRuleOut.model_validate(r) for r in rows],
        total=len(rows), page=1, size=len(rows) or 1)


def _ensure_space_leg(
    session: Session, household_id: int, actor: str,
    parent: Account, space: Account, rule: TransferRule,
) -> None:
    """Paired internal main→space rule for a space destination
    (issue #345): the bank only moves main→main externally, so the
    external leg targets the space's parent account and this derived
    leg lands the money in the space. Skipped when an active rule
    already covers the same parent→space flow."""
    exists = session.scalar(select(TransferRule).where(
        TransferRule.household_id == household_id,
        TransferRule.source_account_id == parent.id,
        TransferRule.dest_account_id == space.id,
        TransferRule.active.is_(True)))
    if exists is not None:
        return
    leg = TransferRule(
        household_id=household_id, name=f"{rule.name} → {space.name}",
        source_account_id=parent.id, dest_account_id=space.id,
        amount_spec=rule.amount_spec, frequency=rule.frequency,
        day_of_month=rule.day_of_month, start_date=rule.start_date,
        end_date=rule.end_date, notes=rule.notes, active=True)
    session.add(leg)
    session.flush()
    schedule.rematerialize_transfer_rule(session, leg)
    emit(session, household_id, actor, "create", "transfer_rule",
         leg.id, {"name": leg.name, "derived": "space_leg",
                  "for_rule": rule.id})


@router.post("", response_model=schemas.TransferRuleOut, status_code=201)
def create_rule(
    body: schemas.TransferRuleIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.TransferRuleOut:
    return _create_rule(body, household, session, actor)


def _create_rule(
    body: schemas.TransferRuleIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep, *, initial_zero_share: bool = False,
) -> schemas.TransferRuleOut:
    _check_accounts(session, household.id, body.source_account_id,
                    body.dest_account_id)
    amount_spec = body.amount_spec.model_dump()
    if initial_zero_share:
        amount_spec.update(pence=0, original_pence=0, effective_amounts=[
            {"date": body.start_date.isoformat(), "pence": 0},
        ])
    spec = transfer_rules.parse_spec(amount_spec)
    parent = transfer_rules.space_parent(session, body.dest_account_id)
    external_dest = (parent.id
                     if parent is not None
                     and parent.id != body.source_account_id
                     else body.dest_account_id)
    r = TransferRule(
        household_id=household.id, name=body.name,
        source_account_id=body.source_account_id,
        dest_account_id=external_dest,
        amount_spec=json.dumps(amount_spec),
        frequency=body.frequency, day_of_month=body.day_of_month,
        start_date=body.start_date, end_date=body.end_date,
        notes=body.notes, active=True)
    session.add(r)
    session.flush()
    schedule.rematerialize_transfer_rule(session, r)
    emit(session, household.id, actor, "create", "transfer_rule", r.id,
         {"name": r.name, "kind": spec.kind.value})
    if parent is not None and external_dest != body.dest_account_id:
        space = session.get(Account, body.dest_account_id)
        if space is not None:
            _ensure_space_leg(session, household.id, actor, parent,
                              space, r)
    return schemas.TransferRuleOut.model_validate(r)


@router.patch("/{rid}", response_model=schemas.TransferRuleOut)
def update_rule(
    rid: int, body: schemas.TransferRulePatch,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.TransferRuleOut:
    return _update_rule(rid, body, household, session, actor)


def _update_rule(
    rid: int, body: schemas.TransferRulePatch, household: HouseholdDep,
    session: SessionDep, actor: ActorDep, *, replan: bool = True,
) -> schemas.TransferRuleOut:
    r = _get(session, household.id, rid)
    prior_pot = r.dest_account_id
    changes = body.model_dump(exclude_unset=True)
    # Explicit null is only meaningful for nullable columns — drop it
    # elsewhere rather than breaking the NOT NULL constraint on flush.
    for k in ("name", "source_account_id", "dest_account_id",
              "frequency", "day_of_month", "start_date", "active"):
        if changes.get(k) is None:
            changes.pop(k, None)
    if {"source_account_id", "dest_account_id"} & changes.keys():
        _check_accounts(
            session, household.id,
            changes.get("source_account_id", r.source_account_id),
            changes.get("dest_account_id", r.dest_account_id))
    prior_spec = json.loads(r.amount_spec)
    explicit_day = "day_of_month" in changes
    if "amount_spec" in changes and changes["amount_spec"] is not None:
        next_spec = changes["amount_spec"]
        if next_spec["kind"] == "fixed" and prior_spec.get("on_member_payday") and not explicit_day:
            next_spec["on_member_payday"] = True
            if "payday_cycles" in prior_spec:
                next_spec["payday_cycles"] = prior_spec["payday_cycles"]
        changes["amount_spec"] = json.dumps(next_spec)
    elif explicit_day and prior_spec.pop("on_member_payday", False):
        prior_spec.pop("payday_cycles", None)
        changes["amount_spec"] = json.dumps(prior_spec)
    endpoints_changed = any(k in changes and changes[k] != getattr(r, k)
                            for k in ("source_account_id", "dest_account_id"))
    if endpoints_changed:
        matched_future = session.scalar(select(ExpectedMovement.id).join(
            ExpectedMatch, ExpectedMatch.movement_id == ExpectedMovement.id).where(
            ExpectedMovement.household_id == household.id,
            ExpectedMovement.source_type == ExpectedSource.transfer_rule,
            ExpectedMovement.source_id == r.id,
            ExpectedMovement.expected_date >= date.today()).limit(1))
        if matched_future is not None:
            raise HTTPException(409, "This rule has matched future movements. "
                                "Create a separate rule for the new accounts.")
        _retain_endpoints(session, r)
    schedule_fields = {"amount_spec", "source_account_id", "dest_account_id",
                       "frequency", "day_of_month", "start_date", "end_date", "active"}
    schedule_changed = any(changes[field] != getattr(r, field)
                           for field in changes.keys() & schedule_fields)
    if schedule_changed:
        shared_contribution_approvals.invalidate_for_rule(session, r)
    for k, v in changes.items():
        setattr(r, k, v)
    session.flush()
    # A space destination auto-derives the paired internal leg — the
    # external leg must land on the space's parent main account (#345).
    space = session.get(Account, r.dest_account_id)
    parent = transfer_rules.space_parent(session, r.dest_account_id)
    if (parent is not None and space is not None
            and parent.id != r.source_account_id):
        r.dest_account_id = parent.id
        session.flush()
        _ensure_space_leg(session, household.id, actor, parent,
                          space, r)
    # spec/timing edits regenerate future rows only — history untouched
    schedule.rematerialize_transfer_rule(session, r)
    if replan and schedule_changed:
        for pot_id in {prior_pot, r.dest_account_id}:
            shared_contribution_approvals.reassess_pending(session, household.id, pot_id)
    emit(session, household.id, actor, "update", "transfer_rule", r.id,
         {"fields": sorted(changes)})
    return schemas.TransferRuleOut.model_validate(r)


def _shared_pot(session: Session, household_id: int, account_id: int) -> Account:
    pot = session.get(Account, account_id)
    if (pot is None or pot.household_id != household_id or pot.archived
            or pot.owner_profile_id is not None):
        raise HTTPException(404, "shared account not found")
    return pot


@router.get("/shared-split/{account_id}", response_model=schemas.SharedSplitSetup)
def get_shared_split(
    account_id: int, household: HouseholdDep, session: SessionDep, profile: ProfileDep,
) -> schemas.SharedSplitSetup:
    _shared_pot(session, household.id, account_id)
    if not rbac.visible(None, profile) and profile is not None:
        raise HTTPException(404, "shared account not found")
    record = session.get(AppMeta, shared_contribution_approvals.split_key(household.id, account_id))
    today = date.today()
    paydays = {}
    for member_id in session.scalars(select(UserProfile.id).where(
        UserProfile.household_id == household.id, UserProfile.is_active.is_(True),
    )):
        if not rbac.visible(member_id, profile) and profile is not None:
            continue
        payday = budget_periods.next_member_payday(session, household.id, member_id, today)
        if payday is not None:
            paydays[member_id] = payday
    return schemas.SharedSplitSetup(
        policy=schemas.SharedSplitPolicy.model_validate_json(record.value) if record else None,
        next_paydays=paydays,
    )


def _split_amounts(body: schemas.SharedSplitIn) -> dict[int, int]:
    shares = sorted(body.shares, key=lambda share: share.profile_id)
    amounts = {share.profile_id: share.pence for share in shares}
    target = body.target_pence
    policy = body.policy
    if policy is not None:
        policy.percentages = {}
        policy.fixed_pence = {}
        if policy.mode != "fixed_remainder":
            policy.remainder_profile_id = None
    if len(amounts) != len(shares):
        raise HTTPException(422, "each contributor can appear only once")
    if policy is not None and policy.mode in ("equal", "percent"):
        weights = {key: 1 for key in amounts}
        if policy.mode == "percent":
            weights = {share.profile_id: share.percentage_bps or 0 for share in shares}
            if sum(weights.values()) != 10000:
                raise HTTPException(422, "percentages must add to 100")
            policy.percentages = weights
        weight_total = sum(weights.values())
        cumulative = allocated = 0
        for key, weight in weights.items():
            cumulative += weight
            next_total = (2 * target * cumulative + weight_total) // (2 * weight_total)
            amounts[key] = next_total - allocated
            allocated = next_total
    elif policy is not None and policy.mode == "fixed_remainder":
        remainder = policy.remainder_profile_id
        if remainder is None or remainder not in amounts:
            raise HTTPException(422, "select who covers the remaining amount")
        policy.fixed_pence = {key: value for key, value in amounts.items() if key != remainder}
        amounts[remainder] = target - sum(policy.fixed_pence.values())
        if amounts[remainder] < 0:
            raise HTTPException(422, "fixed shares exceed the Shared budget total")
    if sum(amounts.values()) != target:
        raise HTTPException(422, "member shares must add up to the target")
    return amounts


@router.post("/shared-split", status_code=204)
def save_shared_split(
    body: schemas.SharedSplitIn, household: HouseholdDep, session: SessionDep,
    actor: ActorDep, profile: ProfileDep,
) -> None:
    """Apply all of a shared pot's monthly shares in one transaction."""
    if not rbac.write_allowed(None, profile):
        raise HTTPException(403, "role cannot edit shared schedules")
    pot = _shared_pot(session, household.id, body.account_id)
    if transfer_rules.space_parent(session, pot.id) is not None:
        raise HTTPException(409, "choose the shared account that receives the bank transfers")
    amounts = _split_amounts(body)
    active_members = set(session.scalars(select(UserProfile.id).where(
        UserProfile.household_id == household.id, UserProfile.is_active.is_(True))))
    if not amounts.keys() <= active_members:
        raise HTTPException(422, "choose active household contributors")

    accounts = {account.id: account for account in session.scalars(select(Account).where(
        Account.household_id == household.id, Account.owner_profile_id.is_not(None),
        Account.archived.is_(False),
    ))}
    existing = list(session.scalars(select(TransferRule).where(
        TransferRule.household_id == household.id, TransferRule.dest_account_id == pot.id,
        TransferRule.active.is_(True), TransferRule.source_account_id.in_(accounts),
    )))
    submitted = {share.rule_id for share in body.shares if share.rule_id is not None}
    if (len(submitted) != sum(share.rule_id is not None for share in body.shares)
            or not submitted <= {rule.id for rule in existing}
            or any(rule.frequency != Frequency.monthly
                   or json.loads(rule.amount_spec).get("kind") != "fixed" for rule in existing)):
        raise HTTPException(409, "share rules changed or are not fixed monthly amounts")
    rules = {rule.id: rule for rule in existing}
    if any(accounts[rule.source_account_id].owner_profile_id not in amounts for rule in existing):
        raise HTTPException(409, "include each existing contributor")
    shares_changed = False
    for share in body.shares:
        pence = amounts[share.profile_id]
        owned = [r for r in existing
                 if accounts[r.source_account_id].owner_profile_id == share.profile_id]
        if len(owned) > 1:
            raise HTTPException(409, "review overlapping contribution schedules")
        rule = owned[0] if owned else None
        if share.rule_id is not None and rules[share.rule_id] is not rule:
            raise HTTPException(422, "select each member's own payment rule")
        retain_zero = body.policy is not None and body.policy.mode != "amount"
        if rule is None and pence == 0 and not retain_zero:
            continue
        source_id = rule.source_account_id if rule is not None else share.source_account_id
        source = accounts.get(source_id) if source_id is not None else None
        if source is None or source.owner_profile_id != share.profile_id:
            raise HTTPException(422, "select each member's own payment account")
        unchanged = rule is not None and transfer_rules.fixed_amount_at(rule, date.today()) == pence
        if rule is not None and share.rule_id is None:
            same_source = share.source_account_id in (None, rule.source_account_id)
            if not unchanged or not same_source:
                raise HTTPException(409, "a member's schedule changed, refresh the split")
        if unchanged:
            continue
        shares_changed = True
        if not rbac.write_allowed(source.owner_profile_id, profile):
            raise HTTPException(403, "only the account owner or an admin can change this share")
        if rule is not None and pence == 0 and retain_zero:
            shared_contribution_approvals.invalidate_for_rule(session, rule)
            shared_contribution_approvals.set_effective_amount(session, rule, date.today(), 0)
        elif rule is not None:
            patch = (schemas.TransferRulePatch(active=False) if pence == 0 else
                     schemas.TransferRulePatch.model_validate({
                         "amount_spec": {"kind": "fixed", "pence": pence},
                     }))
            _update_rule(rule.id, patch, household, session, actor, replan=False)
        else:
            day = share.day_of_month
            first = date.today()
            if share.on_member_payday:
                payday = budget_periods.next_member_payday(
                    session, household.id, share.profile_id, date.today())
                if payday is None:
                    raise HTTPException(422, "configure a monthly payday or choose a payment day")
                day = payday.day
            if day is None:
                raise HTTPException(422, "choose a payment day for each new share")
            if not share.on_member_payday:
                first = pay_date(
                    PaydayRule(RuleKind.fixed_day, day=day, adjust=ShiftPolicy.none),
                    first.year, first.month,
                )
                if first < date.today():
                    first = advance(first, Frequency.monthly, day)
            created = _create_rule(schemas.TransferRuleIn.model_validate({
                "name": f"{source.name} to {pot.name}"[:120],
                "source_account_id": source.id, "dest_account_id": pot.id,
                "amount_spec": {"kind": "fixed", "pence": max(1, pence)},
                "frequency": "monthly", "day_of_month": day,
                "start_date": first,
            }), household, session, actor, initial_zero_share=pence == 0)
            if share.on_member_payday:
                new_rule = _get(session, household.id, created.id)
                new_rule.amount_spec = json.dumps({
                    **json.loads(new_rule.amount_spec), "on_member_payday": True,
                })
                schedule.rematerialize_transfer_rule(session, new_rule)

    if body.policy is not None:
        guided = shared_contribution_approvals.guided_pot_ids(session, household.id)
        if body.policy.mode != "amount" and guided - {pot.id}:
            raise HTTPException(409, "only one shared account can use the budget-guided split")
        key = shared_contribution_approvals.split_key(household.id, pot.id)
        record = session.get(AppMeta, key)
        prior_policy = (schemas.SharedSplitPolicy.model_validate_json(record.value)
                        if record else None)
        if body.policy.approval is None:
            body.policy.approval = prior_policy.approval if prior_policy else "contributor"
            body.policy.approver_profile_id = (prior_policy.approver_profile_id
                                               if prior_policy else None)
        elif profile is not None and not rbac.can(profile, rbac.Action.admin) and (
            prior_policy is None or (body.policy.approval, body.policy.approver_profile_id)
            != (prior_policy.approval, prior_policy.approver_profile_id)
        ):
            raise HTTPException(403, "only a household admin can change the approval policy")
        if body.policy.approval == "delegated":
            approver_id = body.policy.approver_profile_id
            approver = session.get(UserProfile, approver_id) if approver_id is not None else None
            if (approver is None or approver.id not in active_members
                    or not rbac.can(approver, rbac.Action.write)):
                raise HTTPException(422, "choose a household member who can approve changes")
        else:
            body.policy.approver_profile_id = None
        if body.policy.alternate_account_ids is None:
            body.policy.alternate_account_ids = (prior_policy.alternate_account_ids
                                                 if prior_policy else [])
        else:
            included = set(body.policy.alternate_account_ids)
            prior_included = (set(prior_policy.alternate_account_ids or [])
                              if prior_policy else set())
            if (included != prior_included and profile is not None
                    and not rbac.can(profile, rbac.Action.admin)):
                raise HTTPException(403, "only a household admin can edit receiving accounts")
            if included:
                allowed = set(session.scalars(select(Account.id).where(
                    Account.id.in_(included), Account.id != pot.id,
                    Account.household_id == household.id, Account.archived.is_(False),
                    Account.owner_profile_id.is_(None),
                )))
                if allowed != included:
                    raise HTTPException(422, "choose active shared receiving accounts")
            body.policy.alternate_account_ids = sorted(included)
        if body.policy.approval == "contributor":
            viewers = set(session.scalars(select(UserProfile.id).where(
                UserProfile.id.in_(amounts), UserProfile.role == Role.viewer,
            )))
            if viewers:
                raise HTTPException(422, "choose contributors who can approve their changes")
        saved = body.policy.model_dump_json()
        if len(saved.encode("utf-8")) > 1024:
            raise HTTPException(422, "the contribution split exceeds the saved settings limit")
        if record is None:
            session.add(AppMeta(key=key, value=saved))
        else:
            record.value = saved
        if shares_changed or prior_policy != body.policy:
            session.flush()
            shared_contribution_approvals.reassess_pending(session, household.id, pot.id)
        emit(session, household.id, actor, "update", "shared_split", pot.id,
             {"mode": body.policy.mode})


@router.get(
    "/shared-split/{account_id}/proposals",
    response_model=list[shared_contribution_approvals.ContributionProposal],
)
def contribution_proposals(
    account_id: int, household: HouseholdDep, session: SessionDep, profile: ProfileDep,
) -> list[shared_contribution_approvals.ContributionProposal]:
    _shared_pot(session, household.id, account_id)
    _, changes = shared_contribution_approvals.read(session, household.id, account_id)
    today = date.today()
    return [item for item in changes if item.state == "pending"
            and item.effective_date >= today
            and rbac.visible(item.approver_profile_id or item.profile_id, profile)]


@router.post("/shared-split/{account_id}/proposals/{proposal_id}/{decision}", status_code=204)
def decide_contribution_change(
    account_id: int, proposal_id: str, decision: Literal["accept", "keep"],
    household: HouseholdDep, session: SessionDep, actor: ActorDep, profile: ProfileDep,
) -> None:
    _shared_pot(session, household.id, account_id)
    row, changes = shared_contribution_approvals.read(session, household.id, account_id)
    proposal = next((item for item in changes if item.id == proposal_id), None)
    if proposal is None:
        raise HTTPException(409, "this proposal has been replaced")
    approver_id = proposal.approver_profile_id or proposal.profile_id
    if profile is None or not rbac.write_allowed(approver_id, profile):
        raise HTTPException(403, "only the contributor or an admin can decide this proposal")
    state: Literal["accepted", "kept"] = "accepted" if decision == "accept" else "kept"
    if proposal.state == state:
        return
    if proposal.state != "pending" or proposal.effective_date < date.today():
        raise HTTPException(409, "this proposal has expired")
    rule = _get(session, household.id, proposal.rule_id)
    if (not rule.active or rule.dest_account_id != account_id
            or transfer_rules.fixed_amount_at(rule, proposal.effective_date) != proposal.old_pence):
        raise HTTPException(409, "the agreed schedule has changed")
    if decision == "accept":
        if shared_contribution_approvals.guided_pot_ids(session, household.id) != {account_id}:
            raise HTTPException(409, "choose one budget-guided funding account before approving")
        due = shared_contribution_approvals.next_unpaid_date(
            session, rule, proposal.effective_date)
        source = session.get(Account, rule.source_account_id)
        if (due != proposal.effective_date or source is None
                or source.owner_profile_id != proposal.profile_id):
            raise HTTPException(409, "the scheduled payment date or account has changed")
        shared_contribution_approvals.apply(session, rule, proposal)
    proposal.state = state
    shared_contribution_approvals.close_notification(session, proposal.id)
    shared_contribution_approvals.save(session, household.id, account_id, row, changes)
    if decision == "accept":
        later_budgets = {item.budget_id for item in changes
                         if item.rule_id == rule.id and item.state == "pending"
                         and item.effective_date >= proposal.effective_date}
        for later in session.scalars(select(Budget).where(
            Budget.household_id == household.id, Budget.id.in_(later_budgets),
        ).order_by(Budget.start_date, Budget.id)):
            shared_contribution_approvals.propose(
                session, later, "An earlier Shared contribution changed")

    elif (budget := session.get(Budget, proposal.budget_id)) is not None:
        shared_contribution_approvals.propose(
            session, budget, "A contributor kept their agreed amount", account_id=account_id)
    emit(session, household.id, actor, state, "shared_contribution_proposal", rule.id,
         {"proposal_id": proposal.id, "effective_date": proposal.effective_date.isoformat()})


@router.delete("/{rid}", status_code=204)
def delete_rule(
    rid: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep,
) -> None:
    """Permanent removal (issue #344): every *unmatched* expected
    movement the rule generated goes with it — matched rows stay:
    ``expected_match`` FKs to them and real money already met the
    obligation. Pause is the softer option (PATCH ``active=false``)."""
    r = _get(session, household.id, rid)
    shared_contribution_approvals.invalidate_for_rule(session, r)
    _retain_endpoints(session, r)
    session.execute(
        delete(ExpectedMovement).where(
            ExpectedMovement.source_type == ExpectedSource.transfer_rule,
            ExpectedMovement.source_id == r.id,
            ExpectedMovement.id.not_in(
                select(ExpectedMatch.movement_id))))
    session.delete(r)
    session.flush()
    emit(session, household.id, actor, "delete", "transfer_rule", rid,
         {"name": r.name})


@router.post("/{rid}/materialise", status_code=200)
def materialise_rule(
    rid: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep,
) -> dict[str, int]:
    r = _get(session, household.id, rid)
    n = schedule.rematerialize_transfer_rule(session, r)
    emit(session, household.id, actor, "materialise", "transfer_rule",
         r.id, {"generated": n})
    return {"generated": n}


@router.get("/{rid}/evaluate", response_model=schemas.EvaluateResult)
def evaluate_rule(
    rid: int, household: HouseholdDep, session: SessionDep,
    on: Annotated[date, Query()],
) -> schemas.EvaluateResult:
    r = _get(session, household.id, rid)
    amount = transfer_rules.evaluate(session, r, on)
    kind = transfer_rules.parse_spec(r.amount_spec).kind
    return schemas.EvaluateResult(
        amount_pence=amount,
        confidence=(Confidence.scheduled
                    if kind is AmountKind.fixed else Confidence.estimated))
