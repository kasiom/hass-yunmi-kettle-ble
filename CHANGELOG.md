# Changelog

## 0.3.2

- Replace the repeated fourteen-property watchdog snapshot with one lightweight status read;
  initial subscriptions still provide immediate push updates.
- Add redacted connection diagnostics for the last advertisement RSSI, session uptime,
  successful connections, disconnects, unsolicited pushes, full snapshots, and watchdog reads.
- Keep the recommended watchdog interval at 300 seconds to reduce BLE traffic while retaining
  automatic liveness checks.

## 0.3.1

- Keep a reconnect scheduled when an advertisement arrives during exponential backoff,
  preventing Home Assistant's duplicate-advertisement cache from stranding an online kettle.
- Load entities unavailable until the first complete BLE snapshot instead of forcing a setup
  connection when a mains-switched kettle is offline.
- Store the coordinator in typed `ConfigEntry.runtime_data` and add a BLE beaconkey reauth flow.
- Restore the declared Home Assistant 2025.3 compatibility by using the compatible options-flow
  reload pattern instead of the newer `OptionsFlowWithReload` helper.

## 0.3.0

- Treat a powered-off kettle as a normal offline state: entities load as unavailable and
  reconnect from the next BLE advertisement after power returns.
- Publish property notifications immediately and use the configurable interval only as a
  watchdog snapshot (300 seconds by default).
- Serialize all BLE operations and require the kettle to echo the requested value before a
  write is reported as successful.
- Mark profile temperature and duration controls as configuration entities and add their
  Home Assistant device classes.
- Add redacted diagnostics and Czech translations.
- Use a language-neutral default device name for new entries.
- Report an invalid token separately from ordinary Bluetooth/protocol failures in setup.
- Explain that a 32-character MiIO token cannot replace this model's 24-character BLE
  beaconkey.
- Migrate version 1 entries without changing the domain, device identity, entity unique IDs,
  or existing entity IDs. The unused legacy `target_temp` option is removed.
