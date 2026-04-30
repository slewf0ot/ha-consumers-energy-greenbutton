"""Sensor platform for Consumers Energy Green Button."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ConsumersEnergyConfigEntry
from .const import (
    DOMAIN,
    STAT_ELECTRICITY_ENERGY,
    STAT_ELECTRICITY_COST,
)
from .coordinator import ConsumersEnergyCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConsumersEnergyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors and inject statistics into recorder."""
    coordinator: ConsumersEnergyCoordinator = entry.runtime_data

    entities = [
        ConsumersEnergyReadingCountSensor(coordinator, entry),
        ConsumersEnergyLastUpdatedSensor(coordinator, entry),
    ]
    async_add_entities(entities)

    # Inject historical statistics into HA recorder for Energy Dashboard
    await _inject_statistics(hass, coordinator)

    # Also inject on every coordinator update
    coordinator.async_add_listener(
        lambda: hass.async_create_task(_inject_statistics(hass, coordinator))
    )


async def _inject_statistics(
    hass: HomeAssistant,
    coordinator: ConsumersEnergyCoordinator,
) -> None:
    """Inject Green Button readings into HA statistics for Energy Dashboard."""
    readings = coordinator.latest_readings
    if not readings:
        return

    # Find the last statistic we've already inserted so we don't duplicate
    last_stats = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, STAT_ELECTRICITY_ENERGY, True, {"sum"}
    )

    last_sum = 0.0
    last_dt = None

    if last_stats and STAT_ELECTRICITY_ENERGY in last_stats:
        last_entry = last_stats[STAT_ELECTRICITY_ENERGY][0]
        last_sum = last_entry.get("sum", 0.0) or 0.0
        last_dt = datetime.fromtimestamp(
            last_entry["start"], tz=timezone.utc
        )

    # Build statistics list — only new readings
    energy_stats: list[StatisticData] = []
    cost_stats: list[StatisticData] = []
    running_sum = last_sum

    for reading in readings:
        if last_dt and reading.start <= last_dt:
            continue

        running_sum += reading.value_kwh
        energy_stats.append(
            StatisticData(
                start=reading.start,
                sum=running_sum,
                state=reading.value_kwh,
            )
        )

        if reading.cost_usd is not None:
            cost_stats.append(
                StatisticData(
                    start=reading.start,
                    sum=reading.cost_usd,
                    state=reading.cost_usd,
                )
            )

    if not energy_stats:
        _LOGGER.debug("No new readings to inject into statistics")
        return

    _LOGGER.info(
        "Injecting %d new energy statistics into recorder", len(energy_stats)
    )

    # Energy statistics metadata
    energy_meta = StatisticMetaData(
        has_mean=False,
        has_sum=True,
        name="Consumers Energy Electricity",
        source=DOMAIN,
        statistic_id=STAT_ELECTRICITY_ENERGY,
        unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    )
    async_add_external_statistics(hass, energy_meta, energy_stats)

    # Cost statistics metadata
    if cost_stats:
        cost_meta = StatisticMetaData(
            has_mean=False,
            has_sum=True,
            name="Consumers Energy Electricity Cost",
            source=DOMAIN,
            statistic_id=STAT_ELECTRICITY_COST,
            unit_of_measurement="USD",
        )
        async_add_external_statistics(hass, cost_meta, cost_stats)


class ConsumersEnergyBaseSensor(CoordinatorEntity, SensorEntity):
    """Base class for Consumers Energy sensors."""

    def __init__(
        self,
        coordinator: ConsumersEnergyCoordinator,
        entry,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Consumers Energy Green Button",
            "manufacturer": "Consumers Energy",
            "model": "Green Button Connect",
        }


class ConsumersEnergyReadingCountSensor(ConsumersEnergyBaseSensor):
    """Shows how many interval readings have been collected."""

    _attr_name = "Consumers Energy Reading Count"
    _attr_icon = "mdi:counter"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_reading_count"

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data:
            return self.coordinator.data.get("reading_count", 0)
        return None


class ConsumersEnergyLastUpdatedSensor(ConsumersEnergyBaseSensor):
    """Shows the timestamp of the most recent interval reading."""

    _attr_name = "Consumers Energy Last Reading"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-check"

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_last_reading"

    @property
    def native_value(self) -> datetime | None:
        readings = coordinator_readings(self.coordinator)
        if readings:
            return readings[-1].start
        return None


def coordinator_readings(coordinator: ConsumersEnergyCoordinator):
    """Helper to safely get readings from coordinator."""
    return coordinator.latest_readings or []
