"""Consumers Energy Green Button integration for Home Assistant."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError

from .api import UtilityAPIError
from .const import CONF_AUTHORIZATION_UID, DOMAIN
from .coordinator import ConsumersEnergyCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

from typing import TypeAlias
ConsumersEnergyConfigEntry: TypeAlias = ConfigEntry[ConsumersEnergyCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConsumersEnergyConfigEntry
) -> bool:
    """Set up Consumers Energy Green Button from a config entry."""
    coordinator = ConsumersEnergyCoordinator(
        hass,
        api_token=entry.data[CONF_TOKEN],
        authorization_uid=entry.data[CONF_AUTHORIZATION_UID],
    )

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register service: refresh cached data (free)
    async def handle_refresh_data(call: ServiceCall) -> None:
        """Re-fetch cached data from UtilityAPI and re-inject statistics."""
        _LOGGER.info("Consumers Energy: manual data refresh triggered")
        await coordinator.async_force_full_fetch()

    hass.services.async_register(
        DOMAIN,
        "refresh_data",
        handle_refresh_data,
        schema=vol.Schema({}),
    )

    # Register service: trigger new collection from utility (paid)
    async def handle_trigger_collection(call: ServiceCall) -> None:
        """Trigger a fresh data collection from Consumers Energy via UtilityAPI."""
        confirm = call.data.get("confirm", False)
        if not confirm:
            raise ServiceValidationError(
                "Set confirm: true to acknowledge this action costs money "
                "on UtilityAPI paid plans."
            )

        _LOGGER.warning(
            "Consumers Energy: triggering paid data collection from utility"
        )

        # Get meter UIDs from coordinator
        meter_uids = [str(m["uid"]) for m in coordinator.meters]
        if not meter_uids:
            raise ServiceValidationError(
                "No meters found. Make sure the integration has completed "
                "its initial setup."
            )

        try:
            result = await coordinator._api.trigger_collection(meter_uids)
            _LOGGER.info("Collection triggered successfully: %s", result)
            # Refresh local data after a short delay
            await coordinator.async_force_full_fetch()
        except UtilityAPIError as err:
            raise ServiceValidationError(
                f"Collection trigger failed: {err}. Make sure your UtilityAPI "
                "account has a $50 minimum prepaid balance."
            ) from err

    hass.services.async_register(
        DOMAIN,
        "trigger_collection",
        handle_trigger_collection,
        schema=vol.Schema({vol.Optional("confirm", default=False): bool}),
    )

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ConsumersEnergyConfigEntry
) -> bool:
    """Unload the config entry."""
    hass.services.async_remove(DOMAIN, "refresh_data")
    hass.services.async_remove(DOMAIN, "trigger_collection")
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
