"""UtilityAPI / Green Button client for Consumers Energy."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import aiohttp

_LOGGER = logging.getLogger(__name__)

UTILITYAPI_BASE = "https://utilityapi.com"
ESPI_NS = "http://naesb.org/espi"
ATOM_NS = "http://www.w3.org/2005/Atom"

# Multipliers to convert ESPI powerOfTenMultiplier to Wh
POW10 = {-3: 0.001, -2: 0.01, -1: 0.1, 0: 1, 1: 10, 2: 100, 3: 1000}

# HTTP timeouts. Without these, a stalled UtilityAPI connection hangs the
# coordinator forever and the next 6-hour poll never fires.
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=60, connect=15)
XML_TIMEOUT = aiohttp.ClientTimeout(total=300, connect=15)  # ESPI batch XML can be large

# ESPI flowDirection codes for electricity:
#  1 = Forward (delivered TO customer  -- normal consumption)
#  4 = Reverse (received FROM customer -- solar export)
# 19 = Total / Net (forward minus reverse)
# Consumers Energy publishes all three for every billing period; if we don't
# filter we triple-count. Default to Forward (delivered) which matches
# "consumption" for any non-solar customer; fall back to Total then Reverse.
FLOW_PRIORITY = [1, 19, 4]

# UtilityAPI URLs encode the flow as "_kwh_<N>" near the end of MeterReading
# and ReadingType paths, e.g.
#   .../MeterReading/2067835-1716264000-1714500000_kwh_19/IntervalBlock/000001
# Capture the period key + flow.
_FLOW_RE = re.compile(r"/MeterReading/([^/]+?)_kwh_(\d+)(?:/|$)")


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
        async with self._session.get(url, headers=self._headers, timeout=DEFAULT_TIMEOUT) as resp:
            if resp.status != 200:
                raise UtilityAPIError(f"Auth fetch failed: {resp.status}")
            data = await resp.json()
            return data.get("authorizations", [])

    async def get_meters(self, authorization_uid: str) -> list[dict]:
        """Fetch meters for a given authorization."""
        url = f"{UTILITYAPI_BASE}/api/v2/meters"
        params = {"authorizations": authorization_uid}
        async with self._session.get(url, headers=self._headers, params=params, timeout=DEFAULT_TIMEOUT) as resp:
            if resp.status != 200:
                raise UtilityAPIError(f"Meter fetch failed: {resp.status}")
            data = await resp.json()
            return data.get("meters", [])

    async def get_intervals(self, authorization_uid: str) -> list[dict]:
        """Fetch raw interval JSON for an authorization."""
        url = f"{UTILITYAPI_BASE}/api/v2/intervals"
        params = {"authorizations": authorization_uid}
        async with self._session.get(url, headers=self._headers, params=params, timeout=DEFAULT_TIMEOUT) as resp:
            if resp.status != 200:
                raise UtilityAPIError(f"Interval fetch failed: {resp.status}")
            data = await resp.json()
            return data.get("intervals", [])

    async def get_recent_readings(self, authorization_uid: str) -> list["IntervalReading"]:
        """Fetch recent readings via lightweight JSON API.

        Used for incremental updates -- much faster than the full ESPI XML.
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

    async def get_green_button_xml(self, authorization_uid: str) -> list["IntervalReading"]:
        """Fetch and parse full Green Button ESPI XML for an authorization.

        Returns up to 2 years of hourly readings. Use only on first run or
        when a full historical backfill is needed -- the XML can be very large.
        """
        url = (
            f"{UTILITYAPI_BASE}/DataCustodian/espi/1_1/resource"
            f"/Batch/Subscription/{authorization_uid}"
        )
        async with self._session.get(url, headers=self._headers, timeout=XML_TIMEOUT) as resp:
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
            url, headers=self._headers, json=payload, timeout=DEFAULT_TIMEOUT
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


def _flow_from_url(url: str | None) -> tuple[str, int] | None:
    """Extract (period_key, flow_direction) from a UtilityAPI URL.

    Matches the "MeterReading/<period>_kwh_<flow>" segment that appears
    in both IntervalBlock self-links and their related MeterReading links.
    Returns None if the URL doesn't have that shape.
    """
    if not url:
        return None
    m = _FLOW_RE.search(url)
    if not m:
        return None
    return (m.group(1), int(m.group(2)))


def parse_espi_xml(xml_text: str) -> list[IntervalReading]:
    """Parse Green Button ESPI XML and return interval readings.

    Consumers Energy XML structure:
    - One UsagePoint per meter
    - Per billing period, three ReadingTypes (uom=72 Wh) differing only
      in flowDirection: 1 (delivered), 4 (received), 19 (net)
    - One IntervalBlock per ReadingType, identifiable by the "_kwh_<flow>"
      suffix in its href and related MeterReading link
    - No <cost> in the IntervalReadings (Consumers Energy doesn't provide it)

    Without filtering by flowDirection we'd triple-count: every billing
    period publishes the same intervals under flow=1, flow=4, and flow=19.
    We pick a single flow per billing period using FLOW_PRIORITY.
    """
    readings: list[IntervalReading] = []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as err:
        _LOGGER.error("Failed to parse ESPI XML: %s", err)
        return readings

    # Pass 1: Discover all (period_key, flow) IntervalBlocks present, plus
    # collect the powerOfTenMultiplier from any uom=72 ReadingType.
    available: dict[str, set[int]] = {}  # period_key -> set of flows
    blocks_index: list[tuple[str, int, ET.Element]] = []  # (period, flow, IntervalBlock element)
    wh_multiplier = 1.0
    multiplier_set = False

    for entry in root.findall(f"{{{ATOM_NS}}}entry"):
        content = entry.find(f"{{{ATOM_NS}}}content")
        if content is None:
            continue

        # Capture multiplier from any uom=72 ReadingType
        if not multiplier_set:
            rt = content.find(f"{{{ESPI_NS}}}ReadingType")
            if rt is not None:
                uom_el = rt.find(f"{{{ESPI_NS}}}uom")
                if uom_el is not None and (uom_el.text or "") == "72":
                    mult_el = rt.find(f"{{{ESPI_NS}}}powerOfTenMultiplier")
                    mult = int(mult_el.text or "0") if mult_el is not None else 0
                    wh_multiplier = POW10.get(mult, 1.0)
                    multiplier_set = True

        ib = content.find(f"{{{ESPI_NS}}}IntervalBlock")
        if ib is None:
            continue

        # Identify this block's (period, flow) from either its self link
        # or its related MeterReading link.
        self_link = entry.find(f"{{{ATOM_NS}}}link[@rel='self']")
        self_href = self_link.get("href") if self_link is not None else None

        related_hrefs = [
            l.get("href") for l in entry.findall(f"{{{ATOM_NS}}}link")
            if l.get("rel") == "related"
        ]

        ident = _flow_from_url(self_href)
        if ident is None:
            for h in related_hrefs:
                ident = _flow_from_url(h)
                if ident is not None:
                    break
        if ident is None:
            _LOGGER.debug("IntervalBlock without identifiable period/flow: %s", self_href)
            continue

        period, flow = ident
        available.setdefault(period, set()).add(flow)
        blocks_index.append((period, flow, ib))

    if not blocks_index:
        _LOGGER.warning("ESPI XML contained no IntervalBlocks we could identify")
        return readings

    # Pick the highest-priority flow per period
    chosen: set[tuple[str, int]] = set()
    for period, flows in available.items():
        for preferred in FLOW_PRIORITY:
            if preferred in flows:
                chosen.add((period, preferred))
                break
        else:
            chosen.add((period, next(iter(flows))))

    _LOGGER.debug(
        "ESPI: %d billing periods, %d total IntervalBlocks, kept %d (one per period)",
        len(available), len(blocks_index), len(chosen),
    )

    # Pass 2: Parse only the chosen IntervalBlocks
    for period, flow, ib in blocks_index:
        if (period, flow) not in chosen:
            continue
        for ir in ib.findall(f"{{{ESPI_NS}}}IntervalReading"):
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

    # Final dedupe by start timestamp (defense in depth -- if any two
    # billing periods overlap, keep one reading per hour).
    deduped: dict[datetime, IntervalReading] = {}
    for r in readings:
        if r.start not in deduped:
            deduped[r.start] = r
    readings = sorted(deduped.values(), key=lambda r: r.start)

    _LOGGER.info(
        "ESPI: parsed %d unique interval readings", len(readings),
    )
    return readings
