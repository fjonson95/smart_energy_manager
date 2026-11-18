"""Smart Energy Manager integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, PLATFORMS
from .coordinator import SmartEnergyCoordinator

_LOGGER = logging.getLogger(__name__)

# Entiteter som togs bort med V-planeraren (fas B5.4.1–B5.4.2); utan
# städningen ligger de kvar som "inte längre tillhandahållen" i registret.
_REMOVED_UNIQUE_IDS = (
    ("sensor", "sem_plan_b_action"),
    ("sensor", "sem_plan_b_charge_power"),
    ("sensor", "sem_plan_b_discharge_power"),
    ("sensor", "sem_plan_b_reason"),
    ("sensor", "sem_active_planner"),
    ("select", "sem_planner_select"),
    # B5.4.2: opportunistisk nätladdning (steg 4) borttagen – planen nätladdar.
    ("switch", "sem_opportunistic_charge"),
)


def _remove_retired_entities(hass: HomeAssistant) -> None:
    registry = er.async_get(hass)
    for platform, unique_id in _REMOVED_UNIQUE_IDS:
        entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id)
        if entity_id:
            registry.async_remove(entity_id)
            _LOGGER.info("Tog bort borttagen entitet %s", entity_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Smart Energy Manager from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    _remove_retired_entities(hass)

    coordinator = SmartEnergyCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    async def _handle_reset_battery_cost(call: ServiceCall) -> None:
        for coord in hass.data.get(DOMAIN, {}).values():
            if hasattr(coord, "reset_battery_cost"):
                coord.reset_battery_cost()
                _LOGGER.info("Batterikostnad återställd till 0")

    hass.services.async_register(DOMAIN, "reset_battery_cost", _handle_reset_battery_cost)

    async def _handle_export_history(call: ServiceCall) -> None:
        path = call.data.get("path")
        for coord in hass.data.get(DOMAIN, {}).values():
            if hasattr(coord, "async_export_history"):
                await coord.async_export_history(path)

    hass.services.async_register(DOMAIN, "export_history", _handle_export_history)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options updates."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: SmartEnergyCoordinator = hass.data[DOMAIN][entry.entry_id]
    try:
        await coordinator.async_zero_battery()
    except Exception:
        _LOGGER.exception("Kunde inte nollställa batteriet vid avlastning")

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
