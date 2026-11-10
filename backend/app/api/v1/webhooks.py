"""Starling V2 webhook receiver (issue #422, spike doc #417).

Opt-in: ``main.create_app`` only includes this router when
``FF_STARLING_WEBHOOK_PUBLIC_KEY`` is configured, so an
unconfigured deployment exposes no endpoint at all. Starling signs
each delivery SHA512withRSA with the private key of the webhook's
key pair; we verify against the configured public key.

Verified deliveries never write the ledger — they flag
``refresh_requested`` on the affected account's sync cursor and the
scheduler drains it through the normal pipeline (discovery →
balances → feed) within one tick. No new write path, no new sync
logic.
"""
from __future__ import annotations

import base64
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.api.deps import SessionDep
from app.core.settings import Settings
from app.domain import sync as sync_domain
from app.persistence.models import (
    BankAccountMapping,
    MappingStatus,
    TransactionSource,
)

router = APIRouter(tags=["integrations"])


def _pem(key: str) -> bytes:
    """Starling supplies the public key as a bare base64 body
    (begins ``MII``) or full PEM — normalise either."""
    body = key.strip()
    if "BEGIN PUBLIC KEY" not in body:
        body = ("-----BEGIN PUBLIC KEY-----\n" + body
                + "\n-----END PUBLIC KEY-----")
    return body.encode()


def _uids(payload: dict[str, Any]) -> tuple[str, str]:
    """Feed-item and standing-order deliveries carry the provider
    account uid (space deliveries add a category uid); both appear
    top-level or nested under ``content`` across event types."""
    content = payload.get("content")
    if not isinstance(content, dict):
        content = {}
    account = payload.get("accountUid") or content.get("accountUid")
    category = (payload.get("categoryUid")
                or content.get("categoryUid") or "")
    return (account if isinstance(account, str) else "",
            category if isinstance(category, str) else "")


@router.post("/integrations/starling/webhook")
async def starling_webhook(request: Request,
                           session: SessionDep) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    public_key = settings.starling_webhook_public_key
    if not public_key:
        # The router is only included when configured; this guards
        # an app assembled differently.
        raise HTTPException(404)
    body = await request.body()
    signature = request.headers.get("x-hook-signature", "")
    try:
        key = serialization.load_pem_public_key(_pem(public_key))
        if not isinstance(key, rsa.RSAPublicKey):
            raise TypeError("webhook public key is not RSA")
        key.verify(base64.b64decode(signature, validate=True),
                   body, padding.PKCS1v15(), hashes.SHA512())
    except (InvalidSignature, ValueError, TypeError) as e:
        raise HTTPException(401, "invalid webhook signature") from e
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        raise HTTPException(400, "invalid JSON payload") from e
    if not isinstance(payload, dict):
        raise HTTPException(400, "invalid payload")

    account_uid, category_uid = _uids(payload)
    refreshed = 0
    if account_uid:
        mappings = list(session.scalars(
            select(BankAccountMapping).where(
                BankAccountMapping.external_uid == account_uid,
                BankAccountMapping.status == MappingStatus.linked,
                BankAccountMapping.account_id.is_not(None))).all())
        # Prefer the exact space/category match; a delivery without
        # a category uid refreshes every linked mapping on the
        # account (dedupe makes an extra refresh harmless).
        targets = ([m for m in mappings
                    if m.external_category_uid == category_uid]
                   or mappings)
        for m in targets:
            # Queue, never ingest inline: Starling expects a fast
            # 2xx, and the scheduler drains through the full
            # pipeline with dedupe and backoff intact.
            assert m.account_id is not None
            cursor = sync_domain.cursor_for(
                session, m.account_id, TransactionSource.starling)
            cursor.refresh_requested = True
            refreshed += 1
    # Always 200 after a valid signature — non-2xx triggers
    # Starling retries, and an unmapped account is not an error.
    return {"status": "accepted", "refreshes_queued": refreshed}
