# Komeco Connect (Home Assistant Custom Integration)

Unofficial Home Assistant integration for Komeco devices.

## Disclaimer

- This project is unofficial and not affiliated with Komeco.
- It is based on reverse engineering of app/backend behavior and may break if backend contracts change.
- Use at your own risk.

## Features

- Email/password setup flow (device auto-discovery).
- Architecture prepared for multiple Komeco device types.
- Control entities:
  - `water_heater` with `switch` and `temp_set`.
  - `switch` for power.
  - `number` for `temp_set` and model-dependent `zero_cold_water_mode`.
  - `binary_sensor` for model-dependent `zero_cold_water_mode_status`.
- Telemetry entities from AWS IoT shadow (model-dependent), including:
  - connectivity
  - flame/motor/water/antifreeze states
  - temperatures (inlet/outlet/setpoint)
  - flow, productivity, consumption, mode, error code
- Realtime MQTT-over-WebSocket shadow updates for near-instant state changes.
- Polling fallback (`30s` default) for resilience.

## Value Source Priority

For control state fields (`switch`, `temp_set`, `zero_cold_water_mode`, `zero_cold_water_mode_status`), the integration resolves values in this order:

1. AWS IoT Device Shadow `state.reported`
2. `getGasHeaterParamsDash`
3. Latest `commandHistory-get` entry

Short command overrides are applied to reduce flicker from delayed history refreshes.

## Installation (HACS - Recommended)

1. In Home Assistant, open **HACS -> Integrations -> Custom repositories**.
2. Add `https://github.com/cunhacf/komeco-connect` as type **Integration**.
3. Search for **Komeco Connect** in HACS and install.
4. Restart Home Assistant.
5. Go to **Settings -> Devices & Services -> Add Integration**.
6. Add **Komeco Connect** and enter email/password.

## Manual Installation

1. Copy `custom_components/komeco_connect` from this repo to your Home Assistant config directory under `custom_components/komeco_connect`.
2. Restart Home Assistant.
3. Go to **Settings -> Devices & Services -> Add Integration**.
4. Add **Komeco Connect** and enter email/password.

## Token Handling

- `id_token`, `access_token`, `refresh_token`, and `sub` are stored in config entry data.
- Password is also stored so the integration can recover automatically if Cognito refresh token becomes invalid.
- IAM credentials are fetched from Cognito Identity and refreshed automatically.

## Debug Logging

Add this to `configuration.yaml` to enable integration debug logs:

```yaml
logger:
  logs:
    custom_components.komeco_connect: debug
```

Debug coverage includes:

- config flow auth/discovery
- token refresh/login recovery
- AWS JSON and SigV4 request lifecycle (without dumping secrets)
- shadow candidate selection/failover
- coordinator polling, command overrides, and token persistence
- realtime MQTT connect/subscribe/reconnect/message handling

## Known Limitations

- Some models do not expose `zero_cold_water_mode` fields in cloud shadow/history; these entities will be unavailable.
- Backend command endpoint path is `send-commmand` (three `m`) and must remain unchanged.
- `tuyaId` seen in metadata is not directly usable for local Tuya control in this integration.
