"""Device automation triggers for Family Finance (issue #424).

Surfaces the ``family_finance_*`` bus events from ``events.py`` as
selectable triggers on the integration's device in the Home
Assistant automation editor — users pick "A bill became overdue"
instead of hand-writing an event trigger.

Each ``type`` maps 1:1 to an emitted event; attaching delegates to
the core event trigger, so payload matching/docs stay single-sourced.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .events import (
    EVENT_BILL_DUE,
    EVENT_BILL_OVERDUE,
    EVENT_GOAL_MILESTONE,
    EVENT_LOW_BALANCE,
    EVENT_NOTIFICATION_PENDING,
    EVENT_PAYDAY,
    EVENT_PAYSLIP_REVIEW,
    EVENT_SYNC_FAILED,
)

TRIGGER_TYPES: dict[str, str] = {
    "bill_due": EVENT_BILL_DUE,
    "bill_overdue": EVENT_BILL_OVERDUE,
    "sync_failed": EVENT_SYNC_FAILED,
    "goal_milestone": EVENT_GOAL_MILESTONE,
    "low_balance": EVENT_LOW_BALANCE,
    "notification_pending": EVENT_NOTIFICATION_PENDING,
    "payday": EVENT_PAYDAY,
    "payslip_review": EVENT_PAYSLIP_REVIEW,
}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES)})


async def async_get_triggers(
        hass: HomeAssistant, device_id: str) -> list[dict[str, str]]:
    """Every Family Finance event, offered once per our device.

    Only runs against devices carrying our ``(DOMAIN, entry_id)``
    identifier — never answers for other integrations' devices.
    """
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)
    if device is None:
        return []
    if not any(domain == DOMAIN for domain, _ in device.identifiers):
        return []
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: trigger_type,
        }
        for trigger_type in TRIGGER_TYPES
    ]


async def async_attach_trigger(
        hass: HomeAssistant, config: ConfigType,
        action: TriggerActionType,
        trigger_info: TriggerInfo) -> CALLBACK_TYPE:
    """Delegate to the core event trigger for the mapped event."""
    event_config = {
        event_trigger.CONF_PLATFORM: "event",
        event_trigger.CONF_EVENT_TYPE: TRIGGER_TYPES[config[CONF_TYPE]],
    }
    event_config = event_trigger.TRIGGER_SCHEMA(event_config)
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info)
