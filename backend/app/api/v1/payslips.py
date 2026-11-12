"""Private payslip upload and review workflow."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, SessionDep
from app.api.payroll_scope import authorize_employment
from app.domain import notifications, outbox, payrun, payslips, schedule
from app.domain import overtime as overtime_domain
from app.domain import payroll as payroll_domain
from app.domain.overtime_findings import Candidate, PayslipLine, match_lines
from app.persistence.models import (
    Employment,
    NotificationKind,
    NotificationRule,
    OvertimeEntry,
    OvertimeStatus,
    PayrollRun,
    Payslip,
    PayslipStatus,
    SalaryRevision,
)

router = APIRouter(
    prefix="/employments/{emp_id}/payroll-runs/{run_id}/payslips",
    tags=["payslips"], dependencies=[Depends(authorize_employment)])
MAX_BYTES = 10 * 1024 * 1024
MAGIC_TYPES = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


def _run(session: SessionDep, household_id: int, emp_id: int,
         run_id: int) -> PayrollRun:
    run = session.get(PayrollRun, run_id)
    if run is None or run.employment_id != emp_id:
        raise HTTPException(404, "payroll run not found")
    employment = session.get(Employment, emp_id)
    if employment is None or employment.household_id != household_id:
        raise HTTPException(404, "employment not found")
    return run


def _out(row: Payslip) -> schemas.PayslipOut:
    extracted = json.loads(row.extracted_json or "{}")
    return schemas.PayslipOut(
        id=row.id, payroll_run_id=row.payroll_run_id,
        original_name=row.original_name, size_bytes=row.size_bytes,
        media_type=row.media_type, status=row.status.value,
        extracted=extracted or None,
        warnings=json.loads(row.warnings_json or "[]"),
        overtime_findings=extracted.get("overtime_findings", []))


def _notify_variance(
    session: SessionDep, household_id: int, run_id: int,
    findings: list[dict[str, object]],
) -> None:
    if not any(finding.get("status") not in {"match", "auto_resolved"}
               for finding in findings):
        return
    rules = list(session.scalars(select(NotificationRule).where(
        NotificationRule.household_id == household_id,
        NotificationRule.kind == NotificationKind.payroll_variance,
        NotificationRule.enabled.is_(True))))
    if not rules:
        rules = [notifications.create_rule(
            session, household_id, name="Payroll variance",
            kind=NotificationKind.payroll_variance,
            params={}, channel="in_app", privacy_safe=True)]
    for rule in rules:
        notifications.raise_event(
            session, rule, f"payroll:{run_id}",
            {"title": "Payroll variance needs review",
             "path": f"/salary?run={run_id}"})


def _notify_review_needed(
    session: SessionDep, household_id: int, payslip: Payslip,
) -> None:
    """Raise a ``payslip_review`` event when an upload lands in
    ``needs_review`` — Home Assistant surfaces it as a prompt."""
    run = session.get(PayrollRun, payslip.payroll_run_id)
    if run is not None:
        outbox.emit(
            session, household_id, "payslip.review_required",
            entity_kind="payslip", entity_id=payslip.id,
            payload={"source": "payslip", "status": payslip.status.value,
                     "owner_profile_id": run.employment.profile_id},
            dedup_key=f"payslip:{payslip.id}:review_required")
    rules = list(session.scalars(select(NotificationRule).where(
        NotificationRule.household_id == household_id,
        NotificationRule.kind == NotificationKind.payslip_review,
        NotificationRule.enabled.is_(True))))
    if not rules:
        rules = [notifications.create_rule(
            session, household_id, name="Payslip review",
            kind=NotificationKind.payslip_review,
            params={}, channel="in_app", privacy_safe=True)]
    for rule in rules:
        notifications.raise_event(
            session, rule, f"payslip:{payslip.id}:review",
            {"title": "Payslip extraction needs review",
             "path": "/salary"})


@router.post("", response_model=schemas.PayslipOut, status_code=201)
async def upload_payslip(
    emp_id: int, run_id: int, request: Request, file: UploadFile,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.PayslipOut:
    run = _run(session, household.id, emp_id, run_id)
    first = await file.read(16)
    media_type = next((mime for magic, mime in MAGIC_TYPES
                       if first.startswith(magic)), None)
    if media_type is None:
        raise HTTPException(415, "file must be a PDF, PNG or JPEG")
    digest = hashlib.sha256()
    size = 0
    data = bytearray(first)
    digest.update(first)
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size + len(first) > MAX_BYTES:
            raise HTTPException(413, "payslip exceeds 10 MiB limit")
        digest.update(chunk)
        data.extend(chunk)
    size += len(first)
    sha = digest.hexdigest()
    duplicate = session.scalar(select(Payslip).where(
        Payslip.household_id == household.id,
        Payslip.payroll_run_id == run.id, Payslip.sha256 == sha))
    if duplicate is not None:
        return _out(duplicate)
    base = Path(request.app.state.settings.data_dir) / "payslips"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{uuid.uuid4().hex}{'.pdf' if media_type == 'application/pdf' else '.img'}"
    try:
        path.write_bytes(data)
        extracted, warnings = payslips.extract_text(bytes(data), media_type)
        row = Payslip(
            household_id=household.id, payroll_run_id=run.id,
            original_name=(file.filename or "payslip")[:255],
            private_path=str(path), sha256=sha, size_bytes=size,
            media_type=media_type, status=PayslipStatus.needs_review,
            extracted_json=json.dumps(extracted) if extracted else None,
            warnings_json=json.dumps(warnings))
        session.add(row)
        session.flush()
        session.info.setdefault("payslip_paths", set()).add(path)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    _notify_review_needed(session, household.id, row)
    emit(session, household.id, actor, "create", "payslip", row.id,
         {"payroll_run_id": run.id, "size_bytes": size})
    return _out(row)


@router.get("", response_model=list[schemas.PayslipOut])
def list_payslips(
    emp_id: int, run_id: int, household: HouseholdDep, session: SessionDep,
) -> list[schemas.PayslipOut]:
    _run(session, household.id, emp_id, run_id)
    return [_out(row) for row in session.scalars(select(Payslip).where(
        Payslip.household_id == household.id,
        Payslip.payroll_run_id == run_id).order_by(Payslip.id)).all()]


@router.get("/{payslip_id}/download")
def download_payslip(
    emp_id: int, run_id: int, payslip_id: int,
    household: HouseholdDep, session: SessionDep,
) -> FileResponse:
    _run(session, household.id, emp_id, run_id)
    row = session.scalar(select(Payslip).where(
        Payslip.id == payslip_id, Payslip.household_id == household.id,
        Payslip.payroll_run_id == run_id))
    if row is None:
        raise HTTPException(404, "payslip not found")
    path = Path(row.private_path)
    if not path.is_file():
        raise HTTPException(404, "payslip file is missing")
    return FileResponse(path, media_type=row.media_type,
                        filename=row.original_name)


@router.patch("/{payslip_id}", response_model=schemas.PayslipOut)
def review_payslip(
    emp_id: int, run_id: int, payslip_id: int,
    body: schemas.PayslipReviewPatch,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.PayslipOut:
    run = _run(session, household.id, emp_id, run_id)
    row = session.scalar(select(Payslip).where(
        Payslip.id == payslip_id, Payslip.household_id == household.id,
        Payslip.payroll_run_id == run_id))
    if row is None:
        raise HTTPException(404, "payslip not found")
    extracted = json.loads(row.extracted_json or "{}")
    if row.status is PayslipStatus.confirmed:
        raise HTTPException(409, "confirmed payslip review is immutable")
    lines_submitted = "overtime_lines" in body.model_fields_set
    if lines_submitted:
        existing = extracted.get("overtime_findings", [])
        if any(item.get("resolution") for item in existing):
            raise HTTPException(
                409, "resolved overtime findings are immutable; start a new review version")
        ids = [line.id for line in body.overtime_lines or []]
        if len(ids) != len(set(ids)):
            raise HTTPException(422, "overtime line ids must be unique")
    if "components" in body.model_fields_set:
        extracted["components"] = body.components or {}
    candidates = session.scalars(select(OvertimeEntry).where(
        OvertimeEntry.employment_id == emp_id,
        OvertimeEntry.status == OvertimeStatus.approved,
        OvertimeEntry.work_date >= run.period_start,
        OvertimeEntry.work_date <= run.period_end)).all()
    candidate_values = [
        Candidate(
            entry.id, entry.work_date, Decimal(str(entry.hours)),
            entry.hourly_rate_pence, entry.multiplier_bps)
        for entry in candidates]
    employment = run.employment
    revisions = session.scalars(select(SalaryRevision).where(
        SalaryRevision.employment_id == emp_id,
        SalaryRevision.effective_date <= run.period_end).order_by(
            SalaryRevision.effective_date)).all()
    try:
        multipliers = set(overtime_domain.multipliers(employment).values())
    except (TypeError, ValueError):
        multipliers = set()
    # Rates in force during the window: the latest revision at the period
    # start, plus any revision starting inside the window.
    in_force = [
        r for r in revisions
        if r.effective_date > run.period_start
        or (r == max(
            (rev for rev in revisions
             if rev.effective_date <= run.period_start),
            key=lambda rev: rev.effective_date, default=None))]
    for revision in in_force:
        rate = payroll_domain.hourly_rate_pence(
            revision.annual_salary_pence,
            Decimal(str(employment.contracted_hours)))
        candidate_values.extend(
            Candidate(-revision.id * 1000 - multiplier,
                      revision.effective_date, Decimal("0"), rate, multiplier)
            for multiplier in multipliers)
    for mapping in extracted.get("rate_mappings", {}).values():
        candidate_values.append(Candidate(
            -len(candidate_values) - 1, run.period_start, Decimal("0"),
            int(mapping["hourly_rate_pence"]), int(mapping["multiplier_bps"])))
    prior_payslips = session.scalars(
        select(Payslip).join(
            PayrollRun, PayrollRun.id == Payslip.payroll_run_id).where(
                Payslip.household_id == household.id,
                PayrollRun.employment_id == emp_id,
                Payslip.id != row.id)).all()
    for prior in prior_payslips:
        prior_extracted = json.loads(prior.extracted_json or "{}")
        for mapping in prior_extracted.get("rate_mappings", {}).values():
            candidate_values.append(Candidate(
                -len(candidate_values) - 1, run.period_start, Decimal("0"),
                int(mapping["hourly_rate_pence"]),
                int(mapping["multiplier_bps"])))
    deferred = session.scalars(select(OvertimeEntry).where(
        OvertimeEntry.employment_id == emp_id,
        OvertimeEntry.status == OvertimeStatus.approved,
        OvertimeEntry.work_date > run.period_end,
        OvertimeEntry.work_date <= run.period_end + timedelta(days=45))).all()
    if deferred:
        extracted["deferred_overtime"] = [
            {"entry_id": entry.id, "work_date": entry.work_date.isoformat()}
            for entry in deferred]
    if lines_submitted:
        extracted["overtime_lines"] = [
            {"id": line.id, "hours": str(line.hours),
             "printed_rate_pence": line.printed_rate_pence}
            for line in body.overtime_lines or []]
        extracted["overtime_findings_version"] = (
            int(extracted.get("overtime_findings_version", 0)) + 1)
        findings = match_lines(
            [PayslipLine(line.id, line.hours, line.printed_rate_pence)
             for line in body.overtime_lines or []],
            candidate_values,
            cutoff_start=run.period_start, cutoff_end=run.period_end)
        extracted["overtime_findings"] = [
            json.loads(json.dumps(asdict(finding), default=str))
            for finding in findings]
        for finding in extracted["overtime_findings"]:
            if finding.get("status") == "match":
                finding["status"] = "auto_resolved"
                finding["resolution"] = {
                    "action": "auto_resolved", "actor": "system",
                    "at": datetime.now(UTC).isoformat()}
        _notify_variance(
            session, household.id, run_id, extracted["overtime_findings"])
    row.extracted_json = json.dumps(extracted, separators=(",", ":"))
    if "warnings" in body.model_fields_set:
        row.warnings_json = json.dumps(body.warnings or [])
    row.status = PayslipStatus.needs_review
    emit(session, household.id, actor, "update", "payslip", row.id,
         {"payroll_run_id": run_id})
    return _out(row)


@router.post(
    "/{payslip_id}/overtime-findings/{finding_id}/resolve",
    response_model=schemas.PayslipOut)
def resolve_overtime_finding(
    emp_id: int, run_id: int, payslip_id: int, finding_id: str,
    body: schemas.OvertimeFindingResolveIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.PayslipOut:
    _run(session, household.id, emp_id, run_id)
    row = session.scalar(select(Payslip).where(
        Payslip.id == payslip_id, Payslip.household_id == household.id,
        Payslip.payroll_run_id == run_id))
    if row is None:
        raise HTTPException(404, "payslip not found")
    extracted = json.loads(row.extracted_json or "{}")
    findings = extracted.get("overtime_findings", [])
    finding = next((item for item in findings
                    if str(item.get("line_id")) == finding_id), None)
    if finding is None:
        raise HTTPException(404, "overtime finding not found")
    if finding.get("resolution") is not None:
        raise HTTPException(409, "overtime finding already resolved")
    actions = {
        "keep_logged", "query", "accept_payslip", "snooze",
        "employer_extra", "manual", "map_rate", "add_missing",
    }
    if body.action not in actions:
        raise HTTPException(422, "unsupported overtime resolution action")
    if body.action == "snooze" and (
            body.snooze_until is None
            or body.snooze_until.tzinfo is None
            or body.snooze_until <= datetime.now(UTC)):
        raise HTTPException(422, "snooze requires a future revisit date")
    created_id: int | None = None
    if body.action == "add_missing":
        if finding.get("status") != "paid-extra":
            raise HTTPException(422, "only paid-extra findings can add missing work")
        inferred_rate = finding.get("hourly_rate_pence")
        inferred_multiplier = finding.get("multiplier_bps")
        rate = body.hourly_rate_pence or (
            int(inferred_rate) if inferred_rate is not None else None)
        multiplier = body.multiplier_bps or (
            int(inferred_multiplier) if inferred_multiplier is not None else None)
        if body.work_date is None or body.day_type is None \
                or rate is None or multiplier is None:
            raise HTTPException(
                422, "add_missing requires date, day type, rate and multiplier")
        if inferred_rate is not None and rate != int(inferred_rate) \
                or inferred_multiplier is not None \
                and multiplier != int(inferred_multiplier):
            raise HTTPException(
                422, "rate/multiplier must match the finding's inferred group")
        employment = session.get(Employment, emp_id)
        if employment is None or (
                employment.start_date is not None
                and body.work_date < employment.start_date) or (
                employment is not None and employment.end_date is not None
                and body.work_date > employment.end_date):
            raise HTTPException(422, "missing overtime date is outside employment")
        hours = Decimal(str(finding.get("delta_hours", "0")))
        if hours <= 0:
            raise HTTPException(422, "finding has no positive missing hours")
        if hours > 24:
            raise HTTPException(
                422, "missing hours exceed 24; split across multiple work dates")
        entry = overtime_domain.create_entry(
            session, employment, work_date=body.work_date, hours=hours,
            multiplier_bps=multiplier, day_type=body.day_type,
            notes=f"Payslip finding {finding_id}")
        entry.hourly_rate_pence = rate
        session.flush()
        created_id = entry.id
    if body.action == "map_rate":
        if (body.hourly_rate_pence is None
                or body.hourly_rate_pence <= 0
                or body.multiplier_bps is None):
            raise HTTPException(422, "map_rate requires rate and multiplier")
        rate = int(body.hourly_rate_pence)
        multiplier = int(body.multiplier_bps)
        # The printed rate gets permanently bound to this rate group, so
        # the next payslip resolves it without asking again.
        mappings = extracted.setdefault("rate_mappings", {})
        mappings[str(finding.get("printed_rate_pence"))] = {
            "hourly_rate_pence": rate, "multiplier_bps": multiplier}
        finding["hourly_rate_pence"] = rate
        finding["multiplier_bps"] = multiplier
        if finding.get("status") in ("ambiguous",
                                     "rate-discrepancy-incomplete-history"):
            finding["status"] = "mapped"
    finding["resolution"] = {
        "action": body.action, "reason": body.reason,
        "actor": actor, "at": datetime.now(UTC).isoformat(),
        "snooze_until": body.snooze_until.isoformat()
        if body.snooze_until else None,
        "created_entry_id": created_id,
        "owed_pence": finding.get("employer_owed_pence", 0),
    }
    row.extracted_json = json.dumps(extracted)
    emit(session, household.id, actor, "resolve", "overtime_finding",
         row.id, {"payroll_run_id": run_id, "finding_id": finding_id,
                  "action": body.action})
    session.flush()
    return _out(row)


@router.post("/{payslip_id}/confirm", response_model=schemas.PayslipOut)
def confirm_payslip(
    emp_id: int, run_id: int, payslip_id: int,
    body: schemas.PayslipConfirmIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.PayslipOut:
    run = _run(session, household.id, emp_id, run_id)
    row = session.scalar(select(Payslip).where(
        Payslip.id == payslip_id, Payslip.household_id == household.id,
        Payslip.payroll_run_id == run_id))
    if row is None:
        raise HTTPException(404, "payslip not found")
    if not body.actuals:
        raise HTTPException(422, "enter at least one signed actual component")
    if run.status.value != "forecast":
        raise HTTPException(409, "reconciled history is immutable; use correction")
    payrun.reconcile(session, run, body.actuals)
    schedule.rematerialize_payroll_run(session, run)
    extracted = json.loads(row.extracted_json or "{}")
    extracted["confirmed_actuals"] = {
        kind.value: value for kind, value in body.actuals.items()}
    row.extracted_json = json.dumps(extracted)
    row.status = PayslipStatus.confirmed
    if any(component.variance_pence not in (None, 0)
           for component in run.components):
        rules = list(session.scalars(select(NotificationRule).where(
                NotificationRule.household_id == household.id,
                NotificationRule.kind == NotificationKind.payroll_variance,
                NotificationRule.enabled.is_(True))))
        if not rules:
            rules = [notifications.create_rule(
                session, household.id, name="Payroll variance",
                kind=NotificationKind.payroll_variance,
                params={}, channel="in_app", privacy_safe=True)]
        for rule in rules:
            notifications.raise_event(
                session, rule, f"payroll:{run.id}",
                {"title": "Payroll variance needs review",
                 "path": f"/salary?run={run.id}"})
    emit(session, household.id, actor, "confirm", "payslip", row.id,
         {"payroll_run_id": run_id})
    return _out(row)
