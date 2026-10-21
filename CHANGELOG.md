# Changelog

## 0.3.5

- Remove the redundant BLE dependency and update HACS installation instructions.

## 0.3.4

- Fall back to a direct property read when a restored device-side subscription does not
  replay its current value. This keeps a successfully authenticated session alive instead
  of entering a reconnect loop that can leave every entity unavailable and make the kettle
  beep repeatedly.
- Debounce unexpected RF disconnects for 30 seconds: retain the last confirmed values while
  one advertisement-driven reconnect is attempted after backoff, then mark the device
  unavailable if recovery does not succeed. A powered-off kettle is therefore not hidden.
- Report disconnect-grace state and confirmed-data age in redacted diagnostics.
- Replace the generic device artwork with a custom transparent Mi Smart Kettle Pro icon.

## 0.3.3

- Validate all four required Yunmi GATT characteristics before login and report the exact
  missing or unusable channel instead of continuing with a partial service table.
- Use `BleakClientWithServiceCache`, clear an invalid service cache after a GATT-layout
  failure, and ask Home Assistant for the current best connectable path on every attempt.
- Enter an exponential quiet cooldown after repeated GATT-layout failures: retry once after
  30 seconds, then after 30 minutes, 1 hour, 2 hours, 4 hours, and at most every 6 hours.
  This prevents an unreachable or weakly covered kettle from beeping every five minutes.
- Cancel delayed reconnects while no connectable Bluetooth scanner sees the kettle.
- Extend redacted diagnostics with Bluetooth availability, connection attempts, cache clears,
  the GATT failure streak, quiet-mode state, and a safe protocol error message.

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
