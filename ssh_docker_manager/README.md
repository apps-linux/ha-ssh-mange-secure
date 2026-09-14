# SSH Docker Manager

Phase 1: connects to a remote Linux host over SSH and exposes its Docker
containers to Home Assistant via MQTT discovery — start, stop, restart, and
image updates.

## Requirements on the remote host

You set these up yourself; the add-on never elevates privileges on its own:

1. A dedicated service account (no root):
   ```
   useradd -m -s /usr/sbin/nologin svc_ha_docker
   usermod -aG docker svc_ha_docker
   ```
   The account must log back in (or the SSH connection must be fresh) after
   the group change for it to take effect.
2. SSH key auth for that account — either:
   - **Generate in the add-on**: leave `ssh.key_mode` as `generate`, start the
     add-on, and copy the public key it logs into
     `svc_ha_docker`'s `~/.ssh/authorized_keys`; or
   - **Bring your own key**: set `ssh.key_mode` to `existing` and paste the
     private key into `ssh.private_key`.

## Configuration

| Option | Description |
|---|---|
| `ssh.host` / `ssh.port` / `ssh.username` | Remote connection details |
| `ssh.key_mode` | `generate` or `existing` |
| `ssh.private_key` | PEM private key text, only used when `key_mode: existing` |
| `ssh.private_key_passphrase` | Optional passphrase for the private key |
| `mqtt.use_addon_broker` | Use the Mosquitto add-on's credentials if present (default) |
| `mqtt.host` / `mqtt.port` / `mqtt.username` / `mqtt.password` | Manual broker, used when `use_addon_broker` is off or no add-on broker is found |
| `mqtt.discovery_prefix` | HA MQTT discovery prefix, default `homeassistant` |

## Security notes

- The add-on never requires or requests root on the remote host. Docker
  group membership is effectively root-equivalent on that host (full daemon
  socket access) — that's an inherent property of managing Docker remotely,
  not something this add-on can restrict further.
- The private key lives only in the add-on's `/data` volume.
- The host's SSH key is pinned on first connection and verified on every
  connection after that; if you need protection against a compromised first
  connection, verify the host fingerprint out-of-band before enabling the
  add-on.

## Status

Initial skeleton — architecture in place (SSH transport with Docker socket
forwarding, MQTT discovery for switch/button/update entities, event-driven
state updates, reconnect/backoff). Not yet run against a live Home Assistant
Supervisor, MQTT broker, or Docker host — validate library API details
(`asyncssh`, `aiodocker`, `aiomqtt`) against a real environment before
relying on it.
