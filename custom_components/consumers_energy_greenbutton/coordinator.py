"""Data coordinator for Consumers Energy Green Button."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ConsumersEnergyAPI, IntervalReading, UtilityAPIError
from .const import DOMAIN, SCAN_INTERVAL_HOURS

_LOGGER = logging.getLogger(__name__)


class ConsumersEnergyCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch and cache Green Button data.

    Fetch strategy:
    - First run: fetch full ESPI XML (up to 2 years of history)
    - Subsequent runs: use lightweight JSON intervals API (last ~3 days)
    - Full XML re-fetch: only if JSON shows readings newer than last known
      reading by more than 7 days (indicates a large gap / data reset)
    """

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
        self._full_fetch_done: bool = False
        self._last_reading_dt: datetime | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=SCAN_INTERVAL_HOURS),
        )

    async def _async_update_data(self) -> dict:
        """Fetch new data from UtilityAPI using optimized strategy."""
        try:
            # Always fetch meters (lightweight)
            self.meters = await self._api.get_meters(self.authorization_uid)

            if not self._full_fetch_done:
                # First run — fetch full 2-year history via ESPI XML
                _LOGGER.info(
                    "Consumers Energy: performing initial full XML fetch "
                    "(this may take a moment)"
                )
                self.latest_readings = await self._api.get_green_button_xml(
                    self.authorization_uid
                )
                self._full_fetch_done = True
                if self.latest_readings:
                    self._last_reading_dt = self.latest_readings[-1].start
                _LOGGER.info(
                    "Consumers Energy: initial fetch complete, %d readings loaded",
                    len(self.latest_readings),
                )
            else:
                # Subsequent runs — use fast JSON endpoint
                _LOGGER.debug(
                    "Consumers Energy: incremental update via JSON intervals"
                )
                recent = await self._api.get_recent_readings(self.authorization_uid)

                if recent:
                    latest_json_dt = recent[-1].start

                    # Check if JSON has data much newer than our last reading
                    # (gap > 7 days means we missed something — do a full re-fetch)
                    if (
                        self._last_reading_dt is not None
                        and (latest_json_dt - self._last_reading_dt)
                        > timedelta(days=7)
                    ):
                        _LOGGER.warning(
                            "Consumers Energy: detected data gap > 7 days, "
                            "triggering full XML re-fetch"
                        )
                        self.latest_readings = await self._api.get_green_button_xml(
                            self.authorization_uid
                        )
                    else:
                        # Merge new readings into our existing set
                        existing_starts = {r.start for r in self.latest_readings}
                        new_readings = [
                            r for r in recent if r.start not in existing_starts
                        ]
                        if new_readings:
                            _LOGGER.debug(
                                "Consumers Energy: adding %d new readings from JSON",
                                len(new_readings),
                            )
                            self.latest_readings = sorted(
                                self.latest_readings + new_readings,
                                key=lambda r: r.start,
                            )
                        else:
                            _LOGGER.debug(
                                "Consumers Energy: no new readings since last update"
                            )

                    if self.latest_readings:
                        self._last_reading_dt = self.latest_readings[-1].start

            total_kwh = sum(r.value_kwh for r in self.latest_readings)
            total_cost = sum(
                r.cost_usd
                for r in self.latest_readings
                if r.cost_usd is not None
            )

            return {
                "readings": self.latest_readings,
                "meters": self.meters,
                "total_kwh": total_kwh,
                "total_cost": total_cost,
                "reading_count": len(self.latest_readings),
                "last_reading": self._last_reading_dt,
            }

        except UtilityAPIError as err:
            raise UpdateFailed(f"UtilityAPI error: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}") from err

    async def async_force_full_fetch(self) -> None:
        """Force a full XML re-fetch on next update cycle.

        Called by the refresh_data service to ensure a clean reload.
        """
        self._full_fetch_done = False
        await self.async_request_refresh()
