"""Aggregated /api/v1 router."""

from fastapi import APIRouter

from app.api.v1 import (
    accounts,
    categories,
    commitments,
    households,
    transactions,
    transfers,
)

v1_router = APIRouter()
v1_router.include_router(households.router)
v1_router.include_router(accounts.router)
v1_router.include_router(categories.router)
v1_router.include_router(transactions.router)
v1_router.include_router(transfers.router)
v1_router.include_router(commitments.router)
