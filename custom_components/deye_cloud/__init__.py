"""Deye Cloud integration -- read-only compatibility patch for API deviceListItems.

Replaces the upstream coordinator while retaining the original config entry.
No control operations are registered by this diagnostic/read-only release.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DeyeCloudApiError, DeyeCloudClient
from .const import (
    CONF_APP_ID, CONF_APP_SECRET, CONF_EMAIL, CONF_PASSWORD, CONF_REGION,
    COORDINATOR, DEFAULT_SCAN_INTERVAL, DOMAIN, REGIONS,
)
from .deye_parser import (
    devices_from_response, devices_from_station, inverter_or_battery,
    measurements_from_response, station_id, stations_from_response, unwrap,
)

_LOGGER = logging.getLogger(__name__)
# IMPORTANT: read-only until the exact device mapping and write endpoints are verified.
PLATFORMS = [Platform.SENSOR]

# The station/device inventory rarely changes, while the Deye Cloud OpenAPI is
# quota limited. Re-read it at most this often (seconds); telemetry is still
# fetched on every poll.
INVENTORY_INTERVAL = 3600
# If the last discovery found no inverters, retry sooner than the full interval.
INVENTORY_RETRY_INTERVAL = 600


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    base_url = REGIONS[entry.data[CONF_REGION]]["base_url"]
    client = DeyeCloudClient(
        base_url=base_url,
        app_id=entry.data[CONF_APP_ID],
        app_secret=entry.data[CONF_APP_SECRET],
        email=entry.data[CONF_EMAIL],
        password=entry.data[CONF_PASSWORD],
    )
    try:
        await client.obtain_token()
        coordinator = DeyeCloudDataUpdateCoordinator(
            hass, client, timedelta(seconds=int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)))
        )
        await coordinator.async_config_entry_first_refresh()
    except (DeyeCloudApiError, UpdateFailed) as err:
        await client.close()
        raise ConfigEntryNotReady(f"Deye Cloud startup failed: {err}") from err
    except Exception:
        await client.close()
        raise
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {COORDINATOR: coordinator}
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    coordinator = hass.data[DOMAIN].pop(entry.entry_id)[COORDINATOR]
    await coordinator.client.close()
    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


class DeyeCloudDataUpdateCoordinator(DataUpdateCoordinator):
    """Fetch both station/latest and device/latest using documented API shapes."""

    def __init__(self, hass: HomeAssistant, client: DeyeCloudClient, update_interval: timedelta):
        self.client = client
        self.stations: list[int | str] = []
        self.devices: list[str] = []
        # Cached inventory: list of (station dict, [device dicts]) and when it was read.
        self._inventory: list[tuple[dict, list[dict]]] = []
        self._inventory_read = 0.0
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=update_interval)

    async def _fetch_stations(self) -> list[dict]:
        stations: list[dict] = []
        for endpoint in ("/station/listWithDevice", "/station/list"):
            try:
                # Up to 250 stations in 5 requests; normal home accounts need one.
                for page in range(1, 6):
                    response = await self.client._request("POST", endpoint, {"page": page, "size": 50})
                    chunk = stations_from_response(response)
                    stations.extend(chunk)
                    if len(chunk) < 50:
                        break
                if stations:
                    return stations
            except DeyeCloudApiError as err:
                _LOGGER.warning("Deye Cloud station discovery via %s failed: %s", endpoint, err)
        return []

    async def _fetch_station_devices(self, sid: int | str) -> list[dict]:
        # API /station/device is explicitly available to personal user accounts.
        result: list[dict] = []
        for page in range(1, 6):
            response = await self.client._request(
                "POST", "/station/device", {"page": page, "size": 100, "stationIds": [sid]}
            )
            chunk = devices_from_response(response)
            result.extend(chunk)
            if len(chunk) < 100:
                break
        return result

    async def _discover_inventory(self) -> list[tuple[dict, list[dict]]]:
        """Read stations and their devices (the slow-changing part)."""
        stations = await self._fetch_stations()
        inventory: list[tuple[dict, list[dict]]] = []
        for station in stations:
            sid = station_id(station)
            if sid is None:
                continue
            devices = devices_from_station(station)
            if not devices:
                try:
                    devices = await self._fetch_station_devices(sid)
                except DeyeCloudApiError as err:
                    _LOGGER.warning("Deye Cloud station %s device discovery failed: %s", sid, err)
                    devices = []
            inventory.append((station, devices))
        return inventory

    async def _get_inventory(self) -> list[tuple[dict, list[dict]]]:
        """Return the cached inventory, refreshing it only when it is due."""
        now = time.monotonic()
        has_devices = any(devices for _, devices in self._inventory)
        max_age = INVENTORY_INTERVAL if has_devices else INVENTORY_RETRY_INTERVAL
        if self._inventory and now - self._inventory_read < max_age:
            return self._inventory
        try:
            inventory = await self._discover_inventory()
        except DeyeCloudApiError:
            if self._inventory:
                _LOGGER.debug("Deye Cloud inventory refresh failed; reusing cached list")
                return self._inventory
            raise
        if inventory:
            self._inventory = inventory
            self._inventory_read = now
        return inventory or self._inventory

    async def _async_update_data(self) -> dict:
        try:
            inventory = await self._get_inventory()
            if not inventory:
                raise UpdateFailed("Deye Cloud did not return any stations")
            data: dict = {"stations": {}, "devices": {}}
            discovered: dict[str, dict] = {}
            for station, devices in inventory:
                sid = station_id(station)
                try:
                    latest = await self.client.get_station_latest_data(sid)
                    latest = unwrap(latest)
                except DeyeCloudApiError as err:
                    _LOGGER.warning("Deye Cloud station %s latest unavailable: %s", sid, err)
                    latest = {}
                data["stations"][sid] = {"info": station, "data": latest}
                for device in devices:
                    if inverter_or_battery(device):
                        sn = str(device["deviceSn"])
                        discovered[sn] = device
            sns = list(discovered)
            for start in range(0, len(sns), 10):
                batch = sns[start : start + 10]
                try:
                    latest_response = await self.client.get_device_latest_data(batch)
                    readings = measurements_from_response(latest_response)
                except DeyeCloudApiError as err:
                    _LOGGER.warning("Deye Cloud telemetry request failed for batch %d: %s", start // 10 + 1, err)
                    readings = {}
                for sn in batch:
                    device = discovered[sn]
                    telemetry = readings.get(sn, {"fields": {}})
                    data["devices"][sn] = {
                        "info": device,
                        "data": telemetry["fields"],
                        "collectionTime": telemetry.get("collectionTime"),
                    }
            self.stations = list(data["stations"])
            self.devices = list(data["devices"])
            _LOGGER.info(
                "Deye Cloud: found %d stations, %d inverter/battery devices; measurements %s",
                len(self.stations), len(self.devices),
                [len(v["data"]) for v in data["devices"].values()],
            )
            if not discovered:
                _LOGGER.warning(
                    "No inverters discovered. Check station deviceListItems or /station/device permissions."
                )
            return data
        except DeyeCloudApiError as err:
            raise UpdateFailed(f"Deye Cloud API error: {err}") from err
