"""Parse Deye Cloud API responses without assumptions about older envelope shapes.

Local, dependency-free helpers to make the integration straightforward to test.
"""
from __future__ import annotations

from typing import Any


def unwrap(response: Any) -> dict[str, Any]:
    """Handle the root or nested data field from API responses."""
    if not isinstance(response, dict):
        return {}
    inner = response.get("data")
    if isinstance(inner, dict) and any(
        name in inner for name in (
            "stationList", "deviceListItems", "deviceDataList", "deviceList", "devices",
        )
    ):
        return inner
    return response


def stations_from_response(response: Any) -> list[dict[str, Any]]:
    """Return valid stations; their IDs can be int or string."""
    result = unwrap(response)
    stations = result.get("stationList", [])
    return [s for s in stations if isinstance(s, dict) and (s.get("id") is not None or s.get("stationId") is not None)] if isinstance(stations, list) else []


def station_id(station: dict[str, Any]) -> int | str | None:
    return station.get("id", station.get("stationId"))


def devices_from_station(station: dict[str, Any]) -> list[dict[str, Any]]:
    """Deye currently uses deviceListItems, not deviceList."""
    for key in ("deviceListItems", "deviceList", "devices"):
        items = station.get(key)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict) and item.get("deviceSn") is not None]
    return []


def devices_from_response(response: Any) -> list[dict[str, Any]]:
    result = unwrap(response)
    for key in ("deviceListItems", "deviceList", "devices"):
        items = result.get(key)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict) and item.get("deviceSn") is not None]
    return []


def inverter_or_battery(device: dict[str, Any]) -> bool:
    """Exclude loggers (COLLECTOR) that usually have no inverter telemetry."""
    device_type = str(device.get("deviceType") or device.get("type") or "").upper()
    return not device_type or device_type in {"INVERTER", "MICRO_INVERTER", "BATTERY"}


def measurements_from_response(response: Any) -> dict[str, dict[str, Any]]:
    """Map device/latest's deviceDataList to SN -> device metadata + key/value/unit.

    Unlike upstream, never assume the response itself is indexed by serial number.
    """
    result = unwrap(response)
    rows = result.get("deviceDataList", result.get("devices", []))
    if not isinstance(rows, list):
        return {}
    by_sn: dict[str, dict[str, Any]] = {}
    for item in rows:
        if not isinstance(item, dict) or item.get("deviceSn") is None:
            continue
        sn = str(item["deviceSn"])
        fields: dict[str, dict[str, Any]] = {}
        entries = item.get("dataList", [])
        if isinstance(entries, list):
            for field in entries:
                if not isinstance(field, dict) or not field.get("key"):
                    continue
                key = str(field["key"])
                fields[key] = {"value": field.get("value"), "unit": field.get("unit")}
        # Also preserve scalar fields in alternate API forms, but never tokens/meta as sensors.
        if not fields:
            for key, value in item.items():
                if key not in {"deviceSn", "deviceType", "deviceState", "collectionTime", "dataList"} and isinstance(value, (int, float, str)):
                    fields[key] = {"value": value, "unit": None}
        if item.get("deviceState") is not None:
            fields.setdefault("deviceState", {"value": item["deviceState"], "unit": None})
        by_sn[sn] = {
            "fields": fields,
            "deviceType": item.get("deviceType"),
            "collectionTime": item.get("collectionTime"),
        }
    return by_sn
