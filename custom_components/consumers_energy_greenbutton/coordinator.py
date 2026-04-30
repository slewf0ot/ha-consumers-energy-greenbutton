"""Data coordinator for Consumers Energy Green Button."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ConsumersEnergyAPI, IntervalReading, UtilityAPIError
from .const import DOMAIN, SCAN_INTERVAL_HOURS

_LOGGER = logging.getLogger(__name__)


class ConsumersEnergyCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch and cache Green Button data."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_token: str,
        authorization_uid: str,
    ) -> None:
        self.authorization_uid = authorization_uid
        self._api = ConsumersEnergyAPI(
            api_token, async_get_clientsession(hass)
        )
        self.latest_readings: list[IntervalReading] = []
        self.meters: list[dict] = []

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=SCAN_INTERVAL_HOURS),
        )

    async def _async_update_data(self) -> dict:
        """Fetch new data from UtilityAPI."""
        try:
            # Fetch meters
            self.meters = await self._api.get_meters(self.authorization_uid)

            # Fetch interval readings via JSON API (faster than XML for recent data)
            intervals = await self._api.get_intervals(self.authorization_uid)

            # Also fetch full Green Button XML for statistics injection
            self.latest_readings = await self._api.get_green_button_xml(
                self.authorization_uid
            )

            # Summarize for sensors
            total_kwh = sum(r.value_kwh for r in self.latest_readings)
            total_cost = sum(
                r.cost_usd for r in self.latest_readings if r.cost_usd is not None
            )

            _LOGGER.debug(
                "Fetched %d readings, %.2f kWh total",
                len(self.latest_readings),
                total_kwh,
            )

            return {
                "readings": self.latest_readings,
                "meters": self.meters,
                "total_kwh": total_kwh,
                "total_cost": total_cost,
                "reading_count": len(self.latest_readings),
            }

        except UtilityAPIError as err:
            raise UpdateFailed(f"UtilityAPI error: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}") from err
