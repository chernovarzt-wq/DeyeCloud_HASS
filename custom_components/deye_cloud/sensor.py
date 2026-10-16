"""Read-only Deye Cloud sensors built from actual API-reported measurement points.

Unlike the upstream sensor.py, this code does not invent battery fields absent
from /device/latest and respects per-measurement units provided by Deye Cloud.
"""
from __future__ import annotations

from hashlib import sha1
import logging
import math
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import COORDINATOR, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Actual field names from Deye's /station/latest endpoint. Station power values
# are reported in watts (verified against /device/latest on live accounts).
STATION_FIELDS = {
    "batterySOC": ("Battery SOC", "%"),
    "generationPower": ("Generation Power", "W"),
    "consumptionPower": ("Consumption Power", "W"),
    "gridPower": ("Grid Power", "W"),
    "batteryPower": ("Battery Power", "W"),
    "chargePower": ("Charge Power", "W"),
    "dischargePower": ("Discharge Power", "W"),
    "purchasePower": ("Purchase Power", "W"),
    "wirePower": ("Wire Power", "W"),
}


def value_as_state(value: Any, numeric_only: bool = False) -> int | float | str | None:
    """Convert an API value to a sensor state.

    Sensors with a unit, device class or state class are numeric in Home
    Assistant and must never receive text (HA raises ValueError), so for those
    ``numeric_only`` makes non-numeric values report as unknown instead.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (float, int)):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None if numeric_only else (value[:250] or None)
        if not math.isfinite(parsed):
            return None
        return int(parsed) if parsed.is_integer() else parsed
    return None


def measurement_description(key: str, unit: str | None) -> dict:
    """Assign HA units/classes only when the cloud supplies a known unit."""
    known = {
        "%": (None, SensorStateClass.MEASUREMENT),
        "W": (SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        "kW": (SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        "V": (SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
        "A": (SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT),
        "Hz": (SensorDeviceClass.FREQUENCY, SensorStateClass.MEASUREMENT),
        "°C": (SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT),
        "℃": (SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT),
        # Daily counters reset at midnight and lifetime counters only grow;
        # TOTAL_INCREASING covers both and enables long-term statistics and the
        # Energy dashboard.
        "kWh": (SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
        "Wh": (SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
    }
    unit = unit if unit in known else None
    dev_class, state_class = known.get(unit, (None, None))
    if unit == "%" and ("soc" in key.lower() or "state of charge" in key.lower()):
        dev_class = SensorDeviceClass.BATTERY
    if unit == "℃":
        unit = "°C"
    return {"unit": unit, "device_class": dev_class, "state_class": state_class}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
    registered: set[tuple] = set()

    def add_missing_sensors():
        result = coordinator.data or {}
        sensors: list[SensorEntity] = []
        for sid, station in result.get("stations", {}).items():
            for key, (name, unit) in STATION_FIELDS.items():
                identity = ("station", str(sid), key)
                if key in station.get("data", {}) and identity not in registered:
                    registered.add(identity)
                    sensors.append(DeyeStationSensor(coordinator, sid, key, name, unit))
        for sn, device in result.get("devices", {}).items():
            count_id = ("device", str(sn), "__data_point_count__")
            if count_id not in registered:
                registered.add(count_id)
                sensors.append(DeyeDevicePointCount(coordinator, sn))
            for key, field in device.get("data", {}).items():
                identity = ("device", str(sn), key)
                if identity not in registered:
                    registered.add(identity)
                    unit = field.get("unit") if isinstance(field, dict) else None
                    sensors.append(DeyeDeviceSensor(coordinator, sn, key, str(unit) if unit else None))
        if sensors:
            _LOGGER.info("Deye Cloud: adding %d verified telemetry sensors", len(sensors))
            async_add_entities(sensors)

    add_missing_sensors()
    # Device metric sets can change when an inverter comes online later.
    entry.async_on_unload(coordinator.async_add_listener(add_missing_sensors))


class DeyeStationSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, sid: int | str, key: str, name: str, unit: str | None):
        super().__init__(coordinator)
        self._sid, self._key = sid, key
        self._attr_name = name
        self._attr_unique_id = f"station_{sid}_{key}_api_fixed"
        if unit == "%":
            self._attr_native_unit_of_measurement = "%"
            self._attr_device_class = SensorDeviceClass.BATTERY
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif unit == "W":
            self._attr_native_unit_of_measurement = "W"
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        record = (self.coordinator.data or {}).get("stations", {}).get(self._sid, {})
        return value_as_state(record.get("data", {}).get(self._key), numeric_only=True)

    @property
    def available(self):
        return self.coordinator.last_update_success and self.native_value is not None

    @property
    def device_info(self):
        record = (self.coordinator.data or {}).get("stations", {}).get(self._sid, {})
        return {
            "identifiers": {(DOMAIN, f"station-{self._sid}")},
            "name": record.get("info", {}).get("name") or f"Station {self._sid}",
            "manufacturer": "Deye",
            "model": "Solar Station",
        }


class DeyeDeviceSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, sn: str, key: str, unit: str | None):
        super().__init__(coordinator)
        self._sn, self._key = str(sn), key
        suffix = sha1(key.encode("utf-8")).hexdigest()[:16]
        self._attr_unique_id = f"{sn}_deye_point_{suffix}"
        self._attr_name = key
        desc = measurement_description(key, unit)
        if desc["unit"]:
            self._attr_native_unit_of_measurement = desc["unit"]
        if desc["device_class"]:
            self._attr_device_class = desc["device_class"]
        if desc["state_class"]:
            self._attr_state_class = desc["state_class"]
        # Any unit/class makes the sensor numeric in Home Assistant.
        self._numeric_only = bool(desc["unit"] or desc["device_class"] or desc["state_class"])

    @property
    def native_value(self):
        record = (self.coordinator.data or {}).get("devices", {}).get(self._sn, {})
        field = record.get("data", {}).get(self._key, {})
        if not isinstance(field, dict):
            return None
        return value_as_state(field.get("value"), numeric_only=self._numeric_only)

    @property
    def available(self):
        return self.coordinator.last_update_success and self.native_value is not None

    @property
    def device_info(self):
        record = (self.coordinator.data or {}).get("devices", {}).get(self._sn, {})
        info = record.get("info", {})
        model = info.get("deviceModel") or info.get("deviceType") or "Inverter"
        return {
            "identifiers": {(DOMAIN, self._sn)},
            "name": info.get("deviceName") or f"Deye {model} {self._sn[-4:]}",
            "manufacturer": "Deye",
            "model": str(model),
            "serial_number": self._sn,
        }


class DeyeDevicePointCount(CoordinatorEntity, SensorEntity):
    """Diagnostic sensor: register a device even if cloud telemetry is empty."""
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "API measurement count"

    def __init__(self, coordinator, sn: str):
        super().__init__(coordinator)
        self._sn = str(sn)
        self._attr_unique_id = f"{sn}_deye_cloud_data_point_count"

    @property
    def native_value(self):
        record = (self.coordinator.data or {}).get("devices", {}).get(self._sn, {})
        return len(record.get("data", {}))

    @property
    def available(self):
        return self.coordinator.last_update_success and self._sn in (self.coordinator.data or {}).get("devices", {})

    @property
    def device_info(self):
        record = (self.coordinator.data or {}).get("devices", {}).get(self._sn, {})
        info = record.get("info", {})
        model = info.get("deviceModel") or info.get("deviceType") or "Inverter"
        return {
            "identifiers": {(DOMAIN, self._sn)},
            "name": info.get("deviceName") or f"Deye {model} {self._sn[-4:]}",
            "manufacturer": "Deye",
            "model": str(model),
            "serial_number": self._sn,
        }
