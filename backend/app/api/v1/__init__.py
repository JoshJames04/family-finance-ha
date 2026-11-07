"""Aggregated /api/v1 router."""

from fastapi import APIRouter

from app.api.v1 import (
    accounts,
    admin,
    budgets,
    categories,
    commitments,
    employments,
    expected,
    households,
    profiles,
    transactions,
    transfer_rules,
    transfers,
)

v1_router = APIRouter()
v1_router.include_router(households.router)
v1_router.include_router(accounts.router)
v1_router.include_router(budgets.router)
v1_router.include_router(categories.router)
v1_router.include_router(transactions.router)
v1_router.include_router(transfers.router)
v1_router.include_router(commitments.router)
v1_router.include_router(profiles.router)
v1_router.include_router(admin.router)
v1_router.include_router(employments.router)
v1_router.include_router(expected.router)
v1_router.include_router(transfer_rules.router)
