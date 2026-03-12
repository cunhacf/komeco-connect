# Changelog

## 0.2.1 (2026-03-12)

- Renamed user-facing integration name to **Komeco Connect**.
- Added device type constants to prepare for future multi-device support.
- Kept internal domain `komeco_gas_heater` for backward compatibility.
- Updated config flow strings and documentation to reflect generic branding + current gas-heater scope.

## 0.2.0 (2026-03-12)

- Added broad debug logging across setup, config flow, API auth, SigV4 requests, polling coordinator, command sends, token persistence, and realtime MQTT lifecycle.
- Updated realtime MQTT signing to match app behavior (Amplify-style `iotdevicegateway` signing), enabling stable instant shadow updates.
- Kept polling as a fallback while prioritizing realtime shadow updates.
- Improved release docs for GitHub usage, debug instructions, and known limitations.

## 0.1.9

- Initial reverse-engineered custom integration with:
  - Cognito auth/token persistence
  - command support (`switch`, `temp_set`, model-dependent zero-cold-water fields)
  - shadow telemetry sensors/binary sensors
  - MQTT shadow subscription support
