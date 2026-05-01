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
        """Fetch raw interval JSON for an authorization."""
        url = f"{UTILITYAPI_BASE}/api/v2/intervals"
        params = {"authorizations": authorization_uid}
        async with self._session.get(url, headers=self._headers, params=params) as resp:
            if resp.status != 200:
                raise UtilityAPIError(f"Interval fetch failed: {resp.status}")
            data = await resp.json()
            return data.get("intervals", [])

    async def get_recent_readings(self, authorization_uid: str) -> list[IntervalReading]:
        """Fetch recent readings via lightweight JSON API.

        Used for incremental updates — much faster than the full ESPI XML.
        UtilityAPI JSON typically returns the last 3 days of hourly data.
        """
        intervals = await self.get_intervals(authorization_uid)
        readings: list[IntervalReading] = []

        for meter_intervals in intervals:
            blocks = meter_intervals.get("readings", [])
            for block in blocks:
                start_str = block.get("start")
                kwh = block.get("kwh")
                if start_str is None or kwh is None:
                    continue
                try:
                    start_dt = datetime.fromisoformat(
                        start_str.replace("Z", "+00:00")
                    )
                    readings.append(
                        IntervalReading(
                            start=start_dt,
                            duration_seconds=3600,
                            value_wh=float(kwh) * 1000,
                            cost_usd=block.get("cost"),
                        )
                    )
                except (ValueError, TypeError):
                    continue

        return sorted(readings, key=lambda r: r.start)

    async def get_green_button_xml(self, authorization_uid: str) -> list[IntervalReading]:
        """Fetch and parse full Green Button ESPI XML for an authorization.

        Returns up to 2 years of hourly readings. Use only on first run or
        when a full historical backfill is needed — the XML can be very large.
        """
        url = (
            f"{UTILITYAPI_BASE}/DataCustodian/espi/1_1/resource"
            f"/Batch/Subscription/{authorization_uid}"
        )
        async with self._session.get(url, headers=self._headers) as resp:
            if resp.status != 200:
                raise UtilityAPIError(f"GB XML fetch failed: {resp.status}")
            xml_text = await resp.text()

        return parse_espi_xml(xml_text)

    async def trigger_collection(self, meter_uids: list[str]) -> dict:
        """Trigger a fresh data collection from the utility for given meters.

        WARNING: This costs money on UtilityAPI paid plans.
        Requires a $50 minimum prepaid account on UtilityAPI.
        """
        url = f"{UTILITYAPI_BASE}/api/v2/meters/historical-collection"
        payload = {"meters": meter_uids}
        async with self._session.post(
            url, headers=self._headers, json=payload
        ) as resp:
            if resp.status not in (200, 201):
                body = await resp.text()
                raise UtilityAPIError(
                    f"Collection trigger failed ({resp.status}): {body}"
                )
            return await resp.json()

    async def validate_token(self) -> bool:
        """Check if the token is valid by fetching authorizations."""
        try:
            await self.get_authorizations()
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
    """Parse Green Button ESPI XML and return interval readings.

    Consumers Energy XML structure:
    - ReadingType/01: uom=72 (Wh), multiplier=0 -- electricity usage (what we want)
    - ReadingType/02: uom=169, multiplier=3 -- ignore (demand or cost data)
    - Single IntervalBlock with all hourly readings
    - No cost element in readings (Consumers Energy does not provide this)
    """
    readings: list[IntervalReading] = []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as err:
        _LOGGER.error("Failed to parse ESPI XML: %s", err)
        return readings

    # Build a map of ReadingType href -> (multiplier, uom)
    reading_types: dict[str, tuple[int, int]] = {}

    for entry in root.findall(f"{{{ATOM_NS}}}entry"):
        self_link = entry.find(f"{{{ATOM_NS}}}link[@rel='self']")
        content = entry.find(f"{{{ATOM_NS}}}content")
        if content is None or self_link is None:
            continue

        reading_type = content.find(f"{{{ESPI_NS}}}ReadingType")
        if reading_type is None:
            continue

        href = self_link.get("href", "")
        mult_el = reading_type.find(f"{{{ESPI_NS}}}powerOfTenMultiplier")
        uom_el = reading_type.find(f"{{{ESPI_NS}}}uom")
        mult = int(mult_el.text or "0") if mult_el is not None else 0
        uom = int(uom_el.text or "72") if uom_el is not None else 72
        reading_types[href] = (mult, uom)
        _LOGGER.debug("ReadingType %s: multiplier=%d uom=%d", href, mult, uom)

    # Find the Wh reading type (uom=72) for electricity consumption
    wh_multiplier = 1.0
    for href, (mult, uom) in reading_types.items():
        if uom == 72:
            wh_multiplier = POW10.get(mult, 1.0)
            _LOGGER.debug(
                "Using ReadingType %s for energy: multiplier=%s uom=72(Wh)",
                href, wh_multiplier,
            )
            break

    # Parse IntervalBlocks -- skip any linked to non-Wh ReadingTypes
    for entry in root.findall(f"{{{ATOM_NS}}}entry"):
        content = entry.find(f"{{{ATOM_NS}}}content")
        if content is None:
            continue

        interval_block = content.find(f"{{{ESPI_NS}}}IntervalBlock")
        if interval_block is None:
            continue

        links = entry.findall(f"{{{ATOM_NS}}}link")
        related_rt = next(
            (
                lnk.get("href") for lnk in links
                if lnk.get("rel") == "related"
                and "ReadingType" in (lnk.get("href") or "")
            ),
            None,
        )
        if related_rt and related_rt in reading_types:
            _, uom = reading_types[related_rt]
            if uom != 72:
                _LOGGER.debug(
                    "Skipping IntervalBlock linked to non-Wh ReadingType %s",
                    related_rt,
                )
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

            start_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc)
            value_wh = raw_value * wh_multiplier

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
