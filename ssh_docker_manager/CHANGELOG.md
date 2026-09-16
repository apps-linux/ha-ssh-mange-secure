# Changelog

All notable changes to the SSH Linux Management add-on are documented here.
This file itself is picked up and shown in the **Changelog** tab of the
add-on's page in Home Assistant.

## 0.7.3

### Docs
- Added this changelog.

## 0.7.2

### Fixed
- `monitoring_disk_paths` reverted to a comma-separated string. A list
  nested inside a `servers[]` entry fails Supervisor's schema validation
  (`Invalid list for option 'monitoring_disk_paths'`), confirmed against a
  live add-on install, even though the top-level `servers` list itself is
  valid.

## 0.7.1

### Changed
- Tried `monitoring_disk_paths` as a list nested inside each server entry.
  **Broken** — reverted in 0.7.2, do not use.

## 0.7.0

### Added
- Support for multiple remote servers in one add-on instance: a new
  `servers` list option (with its own **Add** button in the Configuration
  tab) where each entry has its own host, SSH key, and independent
  Docker/libvirt/monitoring toggles. Each server runs as its own
  self-reconnecting session — one server's failures never affect another's.
- Each server's SSH key is stored separately under `/data/ssh/<slug>`
  instead of one shared location.

### Changed
- **Breaking:** the previous top-level `ssh`/`docker`/`libvirt`/`monitoring`
  config groups are replaced by `servers[]`. Existing configuration must be
  re-entered after updating.

## 0.6.1

### Docs
- Documented that list-type options render with a native **Add** button in
  the Configuration tab's basic form.

## 0.6.0

### Changed
- **Breaking:** MQTT topic root renamed from an opaque hash to
  `ssh_manage_<host>`, readable in an MQTT explorer and distinct per remote
  host. Existing entities' topics change.
- Host monitoring's disk path became a list, so multiple filesystems can be
  monitored at once. Each path is queried independently (not one `df` call
  with multiple arguments), since `df` de-duplicates rows for paths sharing
  a filesystem.

## 0.5.0

### Added
- Host disk and memory monitoring (`monitoring.enabled`), published as
  GB/percent sensors read from `/proc/meminfo` and `df` over the existing
  SSH connection — no agent, no extra group membership needed.

## 0.4.1

### Changed
- Add-on renamed to **SSH Linux Management** (internal slug left unchanged
  so existing installs keep their `/data`).
- Entity names prefixed `Docker:` / `VM:` so container and VM entities are
  distinguishable in the Home Assistant UI.

## 0.4.0

### Added
- libvirt VM discovery and control (start/shutdown/reboot) via `virsh` run
  over the existing SSH connection, with a `libvirt.enabled` toggle
  alongside `docker.enabled`.

## 0.3.6

### Fixed
- The Docker event stream never worked: `aiodocker`'s `ChannelSubscriber`
  is not an async iterator.

## 0.3.5

### Fixed
- `known_hosts` entries for the default SSH port (22) were written in the
  wrong format and could never be verified again on a later connection.
  Added a one-time self-heal migration for hosts already stuck in that
  state.

## 0.3.4

### Fixed
- SSH host-key pinning never actually worked:
  `get_extra_info("server_host_key")` isn't a real `asyncssh` key.

## 0.3.3

### Fixed
- Passphrase-protected SSH keys failed to decrypt (`asyncssh.pbe.KeyEncryptionError`);
  `bcrypt` was missing from the add-on's dependencies.

## 0.3.2

### Added
- Pasted SSH keys are validated on startup, with an actionable error
  (pointing at the YAML block-scalar syntax needed) instead of a raw
  `asyncssh` traceback.

## 0.3.1

### Added
- MQTT connection failures (e.g. the broker rejecting the configured
  credentials) now surface a clear, actionable error instead of a bare
  stack trace.

## 0.3.0

### Removed
- The `upload` SSH key mode. Home Assistant's add-on config screen has no
  real file-picker field type, so it was never more than a manually-typed
  filename — `generate` and `paste` are the two real options.

## 0.2.1

### Fixed
- `mqtt.broker_mode: homeassistant` now falls back to `core-mosquitto:1883`
  when Supervisor hasn't injected the Mosquitto add-on's credentials yet,
  instead of failing outright.

## 0.2.0

### Added
- Explicit `mqtt.broker_mode` choice (`homeassistant` or `external`)
  instead of an implicit fallback.

## 0.1.0

### Added
- Initial add-on: SSH connection to a remote Docker host (its Docker socket
  forwarded over that same SSH connection), MQTT discovery for containers
  with start/stop/restart/update control, and a generated-or-pasted SSH key.
