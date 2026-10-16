[1.2.1] - 2026-10-16
Fixed

- Inverters were never discovered: the station list returns devices in `deviceListItems`, not `deviceList`. Falls back to `/station/device` when a station carries no device list; data loggers (COLLECTOR) are skipped.
- Inverter telemetry was never parsed: `/device/latest` returns `deviceDataList[].dataList[]` (key/value/unit), not a dict keyed by serial number.
- Station sensors stayed "unknown": they now use the real `/station/latest` fields (`batterySOC`, `generationPower`, `consumptionPower`, `gridPower`, `batteryPower`, `chargePower`, `dischargePower`, `purchasePower`, `wirePower`), in W.
- HTTP 500 when opening the integration options on Home Assistant 2025.12+: the options flow no longer assigns the read-only `config_entry`.

Changed

- Device sensors are created dynamically from the measurement points the cloud actually reports, with the units it reports. kWh/Wh sensors use `total_increasing`, so they work with long-term statistics and the Energy dashboard.
- Non-numeric values on numeric sensors report as unknown instead of raising errors.
- The station/device inventory is cached for 1 hour (10 minutes while no inverter has been found) to stay within the Deye Cloud API quota; telemetry is still read every poll.
- New diagnostic sensor "API measurement count" per inverter (0 = the cloud sent no measurement points).
- **Read-only for now:** only the sensor platform is loaded. The switch/select control entities are disabled until the control endpoints are verified against the new data model.

[1.1.0] - 2025-10-07
Added

- Region selection dropdown in configuration flow
 -- EU/EMEA/Asia-Pacific region support
 -- Americas region support

- Email and password authentication (required by Deye Cloud API)
- Automatic SHA256 password hashing
- Better error messages for authentication failures

Changed

- API base URL now determined by selected region
 -- EU: https://eu1-developer.deyecloud.com/v1.0
 -- US: https://us1-developer.deyecloud.com/v1.0

- Authentication method updated to match Deye Cloud Personal User API
