"""Aggregated /api/v1 router."""

from fastapi import APIRouter

from app.api.v1 import (
    accounts,
    admin,
    analytics,
    anomalies,
    banks,
    budgets,
    categories,
    categorisation,
    commitments,
    credentials,
    diagnostics,
    employments,
    energy,
    expected,
    forecast,
    goals,
    households,
    imports,
    integration,
    loans,
    notifications,
    payees,
    payslips,
    profiles,
    reconcile,
    reports,
    scenarios,
    seasonal,
    settings,
    subscriptions,
    sync,
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
v1_router.include_router(forecast.router)
v1_router.include_router(goals.router)
v1_router.include_router(loans.router)
v1_router.include_router(imports.router)
v1_router.include_router(integration.router)
v1_router.include_router(reports.router)
v1_router.include_router(analytics.router)
v1_router.include_router(seasonal.router)
v1_router.include_router(transfer_rules.router)
v1_router.include_router(subscriptions.router)
v1_router.include_router(scenarios.router)
v1_router.include_router(settings.router)
v1_router.include_router(notifications.router)
v1_router.include_router(payees.router)
v1_router.include_router(payslips.router)
v1_router.include_router(categorisation.router)
v1_router.include_router(sync.router)
v1_router.include_router(reconcile.router)
v1_router.include_router(credentials.router)
v1_router.include_router(banks.router)
v1_router.include_router(anomalies.router)
v1_router.include_router(diagnostics.router)
v1_router.include_router(energy.router)
