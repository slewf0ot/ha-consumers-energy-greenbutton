"""Consumers Energy Green Button integration for Home Assistant."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant

from .const import CONF_AUTHORIZATION_UID, DOMAIN
from .coordinator import ConsumersEnergyCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

type ConsumersEnergyConfigEntry = ConfigEntry[ConsumersEnergyCoordinator]


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

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ConsumersEnergyConfigEntry
) -> bool:
    """Unload the config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
