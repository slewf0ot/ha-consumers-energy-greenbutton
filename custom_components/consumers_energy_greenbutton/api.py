"""UtilityAPI / Green Button client for Consumers Energy."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import aiohttp

_LOGGER = logging.getLogger(__name__)

UTILITYAPI_BASE = "https://utilityapi.com"
ESPI_NS = "http://naesb.org/espi"
ATOM_NS = "http://www.w3.org/2005/Atom"

# Multipliers to convert ESPI powerOfTenMultiplier to Wh
POW10 = {-3: 0.001, -2: 0.01, -1: 0.1, 0: 1, 1: 10, 2: 100, 3: 1000}


class UtilityAPIError(Exception):
    """Raised when the API returns an error."""


class ConsumersEnergyAPI:
    """Client for UtilityAPI Green Button data."""

    def __init__(self, api_token: str, session: aiohttp.ClientSession) -> None:
        self._token = api_token
        self._session = session

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    async def get_authorizations(self) -> list[dict]:
        """Fetch all active authorizations."""
        url = f"{UTILITYAPI_BASE}/api/v2/authorizations"
        async with self._session.get(url, headers=self._headers) as resp:
            if resp.status != 200:
                raise UtilityAPIError(f"Auth fetch failed: {resp.status}")
            data = await resp.json()
            return data.get("authorizations", [])

    async def get_meters(self, authorization_uid: str) -> list[dict]:
        """Fetch meters for a given authorization."""
        url = f"{UTILITYAPI_BASE}/api/v2/meters"
        params = {"authorizations": authorization_uid}
        async with self._session.get(url, headers=self._headers, params=params) as resp:
            if resp.status != 200:
                raise UtilityAPIError(f"Meter fetch failed: {resp.status}")
            data = await resp.json()
            return data.get("meters", [])

    async def get_intervals(self, authorization_uid: str) -> list[dict]:
        """Fetch recent interval data for an authorization."""
        url = f"{UTILITYAPI_BASE}/api/v2/intervals"
        params = {"authorizations": authorization_uid}
        async with self._session.get(url, headers=self._headers, params=params) as resp:
            if resp.status != 200:
                raise UtilityAPIError(f"Interval fetch failed: {resp.status}")
            data = await resp.json()
            return data.get("intervals", [])

    async def get_green_button_xml(self, authorization_uid: str) -> list[IntervalReading]:
        """Fetch and parse Green Button ESPI XML for an authorization."""
        url = (
            f"{UTILITYAPI_BASE}/DataCustodian/espi/1_1/resource"
            f"/Batch/Subscription/{authorization_uid}"
        )
        async with self._session.get(url, headers=self._headers) as resp:
            if resp.status != 200:
                raise UtilityAPIError(f"GB XML fetch failed: {resp.status}")
            xml_text = await resp.text()

        return parse_espi_xml(xml_text)

    async def validate_token(self) -> bool:
        """Check if the token is valid by fetching authorizations."""
        try:
            auths = await self.get_authorizations()
            return True
        except UtilityAPIError:
            return False


class IntervalReading:
    """A single energy interval reading."""

    def __init__(
        self,
        start: datetime,
        duration_seconds: int,
        value_wh: float,
        cost_usd: float | None = None,
    ) -> None:
        self.start = start
        self.duration_seconds = duration_seconds
        self.value_wh = value_wh
        self.value_kwh = value_wh / 1000.0
        self.cost_usd = cost_usd

    def __repr__(self) -> str:
        return (
            f"IntervalReading(start={self.start}, "
            f"kwh={self.value_kwh:.4f}, cost={self.cost_usd})"
        )


def parse_espi_xml(xml_text: str) -> list[IntervalReading]:
    """Parse Green Button ESPI XML and return interval readings."""
    readings: list[IntervalReading] = []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as err:
        _LOGGER.error("Failed to parse ESPI XML: %s", err)
        return readings

    # Find power of ten multiplier from ReadingType if present
    pow10_multiplier = 0
    uom = 72  # default: Wh

    for entry in root.findall(f"{{{ATOM_NS}}}entry"):
        content = entry.find(f"{{{ATOM_NS}}}content")
        if content is None:
            continue

        reading_type = content.find(f"{{{ESPI_NS}}}ReadingType")
        if reading_type is not None:
            mult_el = reading_type.find(f"{{{ESPI_NS}}}powerOfTenMultiplier")
            uom_el = reading_type.find(f"{{{ESPI_NS}}}uom")
            if mult_el is not None:
                pow10_multiplier = int(mult_el.text or "0")
            if uom_el is not None:
                uom = int(uom_el.text or "72")

    multiplier = POW10.get(pow10_multiplier, 1.0)

    # Parse IntervalBlocks
    for entry in root.findall(f"{{{ATOM_NS}}}entry"):
        content = entry.find(f"{{{ATOM_NS}}}content")
        if content is None:
            continue

        interval_block = content.find(f"{{{ESPI_NS}}}IntervalBlock")
        if interval_block is None:
            continue

        for ir in interval_block.findall(f"{{{ESPI_NS}}}IntervalReading"):
            time_period = ir.find(f"{{{ESPI_NS}}}timePeriod")
            value_el = ir.find(f"{{{ESPI_NS}}}value")
            cost_el = ir.find(f"{{{ESPI_NS}}}cost")

            if time_period is None or value_el is None:
                continue

            start_el = time_period.find(f"{{{ESPI_NS}}}start")
            duration_el = time_period.find(f"{{{ESPI_NS}}}duration")

            if start_el is None or duration_el is None:
                continue

            try:
                start_ts = int(start_el.text)
                duration_s = int(duration_el.text)
                raw_value = int(value_el.text)
            except (ValueError, TypeError):
                continue

            # Convert Unix timestamp to datetime
            start_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc)

            # Convert value to Wh (uom 72 = Wh, 169 = Wh already)
            value_wh = raw_value * multiplier

            # Cost is in hundred-thousandths of currency unit (e.g. cents * 1000)
            cost_usd = None
            if cost_el is not None and cost_el.text:
                try:
                    cost_usd = int(cost_el.text) / 100000.0
                except (ValueError, TypeError):
                    pass

            readings.append(
                IntervalReading(
                    start=start_dt,
                    duration_seconds=duration_s,
                    value_wh=value_wh,
                    cost_usd=cost_usd,
                )
            )

    _LOGGER.debug("Parsed %d interval readings from ESPI XML", len(readings))
    return sorted(readings, key=lambda r: r.start)
