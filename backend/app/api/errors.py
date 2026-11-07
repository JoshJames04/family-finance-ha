"""RFC 9457-style problem-detail error responses."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


def problem(status: int, title: str, detail: str | None = None,
            type_: str = "about:blank", **extra: Any) -> JSONResponse:
    body: dict[str, Any] = {"type": type_, "title": title, "status": status}
    if detail:
        body["detail"] = detail
    body.update(extra)
    return JSONResponse(body, status_code=status,
                        media_type="application/problem+json")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exc(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return problem(exc.status_code,
                       str(exc.detail) if exc.detail else "HTTP error",
                       type_="about:blank")

    @app.exception_handler(RequestValidationError)
    async def validation_exc(_: Request,
                             exc: RequestValidationError) -> JSONResponse:
        return problem(422, "Validation failed",
                       detail="Request failed schema validation",
                       errors=jsonable_encoder(exc.errors()))

    # Domain-rule violations → 409 Conflict
    from app.domain.accounts import AccountNotEmptyError
    from app.domain.budgets import BudgetError
    from app.domain.categories import CategoryCycleError
    from app.domain.contributions import MatchError
    from app.domain.goals import GoalError
    from app.domain.loans import LoanError
    from app.domain.payroll import PayrollError
    from app.domain.periods import (
        PeriodLockedError,
        ReconciledImmutableError,
    )
    from app.domain.refunds import RefundError
    from app.domain.transactions import InvalidTransitionError
    from app.domain.transfer_rules import TransferRuleError
    from app.domain.transfers import TransferError

    domain_conflicts = (
        AccountNotEmptyError, BudgetError, CategoryCycleError,
        PayrollError, PeriodLockedError, ReconciledImmutableError,
        RefundError, InvalidTransitionError, TransferError,
        TransferRuleError, MatchError, GoalError, LoanError,
    )
    for exc_type in domain_conflicts:
        @app.exception_handler(exc_type)
        async def domain_exc(_: Request, exc: Exception) -> JSONResponse:
            return problem(409, "Domain rule violated", detail=str(exc))

    # Uniqueness/FK violations (e.g. transaction dedup key) → 409.
    from sqlalchemy.exc import IntegrityError

    @app.exception_handler(IntegrityError)
    async def integrity_exc(_: Request,
                            exc: IntegrityError) -> JSONResponse:
        return problem(409, "Conflict with existing data")

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return problem(500, "Internal server error")
