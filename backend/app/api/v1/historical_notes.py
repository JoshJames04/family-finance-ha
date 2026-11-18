"""Private, owner-scoped retained annotations. No inferred record mappings."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import delete, select

from app.api.deps import HouseholdDep, ProfileDep, SessionDep
from app.persistence.models import AppMeta, Role, UserProfile

router = APIRouter(prefix="/historical-notes", tags=["historical-notes"])


class HistoricalNote(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_key: str = Field(min_length=1, max_length=500)
    sheet_index: int = Field(ge=0)
    sheet_name: str = Field(min_length=1, max_length=200)
    year: int = Field(ge=1900, le=9999)
    worksheet_part: str = Field(min_length=1, max_length=300)
    cell_reference: str = Field(pattern=r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")
    thread_id: str | None = Field(default=None, max_length=200)
    timestamp: str | None = Field(default=None, max_length=100)
    text: str = Field(min_length=1, max_length=100000)

    @model_validator(mode="after")
    def valid_timestamp(self) -> HistoricalNote:
        if self.timestamp is not None:
            datetime.fromisoformat(self.timestamp.replace("Z", "+00:00"))
        if not self.text.strip():
            raise ValueError("empty note")
        return self


class HistoricalArchiveIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provenance_status: Literal["retained_extraction_unverified"]
    notes: list[HistoricalNote] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_keys(self) -> HistoricalArchiveIn:
        if len({n.source_key for n in self.notes}) != len(self.notes):
            raise ValueError("duplicate source keys")
        return self


class HistoricalArchiveOut(BaseModel):
    digest: str | None = None
    source_sha256: str | None = None
    provenance_status: Literal["retained_extraction_unverified"] | None = None
    notes: list[HistoricalNote] = Field(default_factory=list)


def _prefix(profile: UserProfile | None, household_id: int) -> str:
    if profile is None or profile.role != Role.admin:
        raise HTTPException(403, "An administrator profile is required")
    # ProfileDep already verifies active membership in the selected household.
    return f"historical:{household_id}:{profile.id}:"


def _read(session: SessionDep, prefix: str) -> HistoricalArchiveOut:
    rows = session.scalars(select(AppMeta).where(
        AppMeta.key.startswith(prefix)).order_by(AppMeta.key)).all()
    if not rows:
        return HistoricalArchiveOut()
    raw = "".join(row.value for row in rows)
    archive = HistoricalArchiveIn.model_validate_json(raw)
    return HistoricalArchiveOut(**archive.model_dump(),
                                digest=hashlib.sha256(raw.encode()).hexdigest())


@router.get("", response_model=HistoricalArchiveOut)
def read_archive(session: SessionDep, household: HouseholdDep,
                 profile: ProfileDep, response: Response) -> HistoricalArchiveOut:
    response.headers["Cache-Control"] = "no-store"
    return _read(session, _prefix(profile, household.id))


@router.put("", response_model=HistoricalArchiveOut)
async def import_archive(request: Request, session: SessionDep,
                         household: HouseholdDep, profile: ProfileDep,
                         response: Response) -> HistoricalArchiveOut:
    prefix = _prefix(profile, household.id)
    # Parse here so validation failures cannot echo private note input through
    # the application's general validation handler or error telemetry.
    raw_body = await request.body()
    if len(raw_body) > 2000000:
        raise HTTPException(413, "Archive exceeds size limit")
    try:
        body = HistoricalArchiveIn.model_validate_json(raw_body)
    except (ValidationError, ValueError):
        raise HTTPException(422, "Invalid historical archive") from None
    raw = json.dumps(body.model_dump(), ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"))
    digest = hashlib.sha256(raw.encode()).hexdigest()
    existing = _read(session, prefix)
    if existing.digest is not None:
        if existing.digest != digest:
            raise HTTPException(409, "Archive already exists; rollback it before replacement")
    else:
        # AppMeta values are limited to 1024 characters. Chunks and their keys
        # are committed together by the request transaction; never truncate.
        session.add_all([AppMeta(key=f"{prefix}{i // 1024:06d}", value=raw[i:i + 1024])
                         for i in range(0, len(raw), 1024)])
        session.flush()
    response.headers["Cache-Control"] = "no-store"
    return HistoricalArchiveOut(**body.model_dump(), digest=digest)


@router.delete("", status_code=204)
def rollback_archive(digest: str, session: SessionDep,
                     household: HouseholdDep, profile: ProfileDep) -> Response:
    prefix = _prefix(profile, household.id)
    existing = _read(session, prefix)
    if existing.digest is None or existing.digest != digest:
        raise HTTPException(409, "Archive digest does not match")
    session.execute(delete(AppMeta).where(AppMeta.key.startswith(prefix)))
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
