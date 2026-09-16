# SSH Linux Management

Connects to a remote Linux host over SSH and exposes it to Home Assistant via
MQTT discovery:
- **Docker** (`docker.enabled`, on by default) — start, stop, restart
  containers, and update images.
- **libvirt VMs** (`libvirt.enabled`, off by default) — start, shut down, and
  reboot VMs managed by libvirt/QEMU.
- **Host monitoring** (`monitoring.enabled`, off by default) — disk and
  memory usage of the remote host itself, as sensors in GB/%.

All three are independent toggles; enable any combination. At least one must
be on.

Entity names are prefixed with `Docker:`, `VM:`, or `Host:` (e.g.
`Docker: my-nginx`, `VM: ubuntu-vm`, `Host: Disk Used (/)`) so it's clear
which is which when more than one is enabled on the same host.

### MQTT topics

Everything is published under `ssh_manage_<host>`, where `<host>` is your
configured `ssh.host` (e.g. `ssh_manage_192_168_1_101` or
`ssh_manage_myserver_local`) — readable in an MQTT explorer, and distinct
per remote host if you run the add-on against more than one. Underneath
that: `.../docker/<container_id>/...`, `.../vm/<domain_name>/...`, and
`.../host/<metric>`.

## Requirements on the remote host

You set these up yourself; the add-on never elevates privileges on its own:

1. A dedicated service account (no root), added to the group(s) for whichever
   feature(s) you enable:
   ```
   useradd -m -s /usr/sbin/nologin svc_ha_docker
   usermod -aG docker svc_ha_docker      # needed for docker.enabled
   usermod -aG libvirt svc_ha_docker     # needed for libvirt.enabled
   ```
   The account must log back in (or the SSH connection must be fresh) after
   a group change for it to take effect. For libvirt, `virsh`/`libvirt-clients`
   must also be installed and `libvirtd` running on the remote host — the
   add-on talks to it by running `virsh` over the same SSH connection, not a
   separate protocol, so no extra port or socket forwarding is needed for it.
2. SSH key auth for that account — pick one:
   - **Generate in the add-on**: leave `ssh.key_mode` as `generate`, start the
     add-on, and copy the public key it logs into
     `svc_ha_docker`'s `~/.ssh/authorized_keys`.
   - **Paste an existing key**: set `ssh.key_mode` to `paste` and put the
     private key text into `ssh.private_key`. The add-on's Configuration tab
     renders that as a single-line box in its basic form — switch to
     **Edit in YAML** (top-right of the tab) to paste the full multi-line
     PEM text comfortably, and use a literal block scalar so YAML keeps the
     line breaks intact:
     ```yaml
     ssh:
       private_key: |
         -----BEGIN OPENSSH PRIVATE KEY-----
         ...
         -----END OPENSSH PRIVATE KEY-----
     ```
     Without the `|`, a plain multi-line YAML value gets its line breaks
     folded into spaces, which corrupts the key and fails with a
     `Missing PEM footer` error. The add-on validates the key on startup and
     will tell you this if it happens.

   There is no `upload` mode: Home Assistant's add-on configuration screen
   has no file-picker field type, so a real "choose file" upload isn't
   possible from here — paste is the only way to bring your own key.

## Configuration

| Option | Description |
|---|---|
| `docker.enabled` | Discover/control Docker containers. Default `true`. |
| `libvirt.enabled` | Discover/control libvirt VMs. Default `false`. |
| `libvirt.connect_uri` | libvirt connection URI used on the remote host, default `qemu:///system` |
| `monitoring.enabled` | Publish host disk/memory usage sensors. Default `false`. |
| `monitoring.disk_paths` | List of paths on the remote host whose filesystem usage is reported, default `["/"]` |
| `ssh.host` / `ssh.port` / `ssh.username` | Remote connection details |
| `ssh.key_mode` | `generate` or `paste` |
| `ssh.private_key` | PEM private key text, only used when `key_mode: paste` |
| `ssh.private_key_passphrase` | Optional passphrase for the private key |
| `mqtt.broker_mode` | `homeassistant` (default, uses the Mosquitto add-on) or `external` (a separate broker) |
| `mqtt.host` / `mqtt.port` / `mqtt.username` / `mqtt.password` | Required in `broker_mode: external`; optional overrides in `broker_mode: homeassistant` (see below) |
| `mqtt.discovery_prefix` | HA MQTT discovery prefix, default `homeassistant` |
| `poll_interval` | Seconds between libvirt VM state polls and host monitoring updates (Docker uses the Docker event stream instead, no polling) |

### Host monitoring

`monitoring.enabled` adds three Memory sensors (Used, Available, Use%) plus
three Disk sensors (Used, Free, Use%) *per path* in `monitoring.disk_paths`
— so `disk_paths: ["/", "/mnt/data"]` gives you two full sets of disk
sensors, named e.g. `Host: Disk Used (/)` and `Host: Disk Used (/mnt/data)`.
Each path is queried independently (not `df /path1 /path2` in one call),
since `df` de-duplicates rows for paths that share a filesystem — e.g. `/`
and `/home` are often the same filesystem — which would otherwise make it
ambiguous which output row belongs to which configured path.

Disk and memory sizes are published as plain numbers with
`unit_of_measurement: GB` (decimal gigabytes, not GiB) so Home Assistant
renders them as e.g. `42.7 GB` and keeps them graphable in
history/statistics — the state itself is never a pre-formatted string like
`"42.7 GB"`, since that would break numeric graphing. No extra group
membership is needed on the remote host: this reads `/proc/meminfo` and
runs `df` on each configured path, both readable by any account.

Home Assistant's add-on options form always shows every field regardless of
other selections — it can't hide `private_key` when `key_mode: generate`, or
the `mqtt.*` broker fields when `broker_mode: homeassistant`. That's a
platform limitation of the add-on config schema, not something this add-on
controls. Fields simply say in this table (and in their description text)
when they're actually used.

In `broker_mode: homeassistant`, the add-on prefers the credentials Supervisor
injects for the Mosquitto add-on. If those aren't present yet (service
discovery not propagated, older Supervisor versions), it falls back to
`core-mosquitto:1883` — the fixed internal address every Home Assistant
install with the Mosquitto add-on installed can reach — using
`mqtt.username`/`mqtt.password` if you've set them (the Mosquitto add-on's
default config only allows anonymous connections from Home Assistant's core
supervisor network, so you may need to add a login for this add-on's account
in the Mosquitto add-on's configuration).

## Security notes

- The add-on never requires or requests root on the remote host. Docker
  group membership is effectively root-equivalent on that host (full daemon
  socket access), and `libvirt` group membership is similarly powerful
  (VMs can be configured with arbitrary host device/disk passthrough) —
  that's an inherent property of managing either remotely, not something
  this add-on can restrict further.
- The private key lives only in the add-on's `/data` volume.
- The host's SSH key is pinned on first connection and verified on every
  connection after that; if you need protection against a compromised first
  connection, verify the host fingerprint out-of-band before enabling the
  add-on.

## Status

Docker support (SSH transport with Docker socket forwarding, MQTT discovery,
event-driven state updates, reconnect/backoff) has been run end-to-end
against a live Home Assistant Supervisor, MQTT broker, and Docker host, with
several real bugs found and fixed along the way (see git history).

libvirt support is new: the `virsh` output parser and the MQTT
discovery/command dispatch/polling logic are covered by targeted tests
against fakes, but the actual `virsh` invocations over SSH have not been
exercised against a real libvirtd. Worth validating `libvirt.connect_uri`,
group permissions, and the start/shutdown/reboot commands against your setup
before relying on it — if `virsh list --all` output looks different than
expected (e.g. a very old or very new libvirt version changes the table
format), the parser in `libvirt_client.py` is the place to look.

Host monitoring is also new: `/proc/meminfo` and `df -P -B1` parsing in
`host_monitor.py` are covered by tests against realistic sample output
(including the older-kernel fallback when `MemAvailable` isn't reported),
and the GB/percent conversion and sensor publishing are covered against a
fake monitor, but it hasn't been exercised against a real remote host over
SSH yet.
