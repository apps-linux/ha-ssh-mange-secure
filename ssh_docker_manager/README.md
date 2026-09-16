# SSH Linux Management

Connects to one or more remote Linux hosts over SSH — each with its own key
and its own settings — and exposes them to Home Assistant via MQTT
discovery:
- **Docker** (`docker_enabled`, on by default per server) — start, stop,
  restart containers, and update images.
- **libvirt VMs** (`libvirt_enabled`, off by default per server) — start,
  shut down, and reboot VMs managed by libvirt/QEMU.
- **Host monitoring** (`monitoring_enabled`, off by default per server) —
  disk and memory usage of the remote host itself, as sensors in GB/%.

All three are independent per-server toggles; enable any combination on each
server. At least one must be on per server.

Entity names are prefixed with `Docker:`, `VM:`, or `Host:` (e.g.
`Docker: my-nginx`, `VM: ubuntu-vm`, `Host: Disk Used (/)`) so it's clear
which is which when more than one is enabled on the same server.

## Adding servers

The Configuration tab has a **Servers** list with an **Add** button (this is
Home Assistant's native list-of-objects UI, not something drawn by the
add-on) — click it to add another server, each with its own host, SSH
key, and Docker/libvirt/monitoring settings. Every server runs as an
independent, self-reconnecting session: one server being unreachable or
misconfigured never affects any other server's connection, discovery, or
entities.

### MQTT topics

Each server publishes under `ssh_manage_<host>`, where `<host>` is that
server's configured `host` field (e.g. `ssh_manage_192_168_1_101` or
`ssh_manage_myserver_local`) — readable in an MQTT explorer, and guaranteed
distinct from your other configured servers as long as their `host` values
are distinct. Underneath that: `.../docker/<container_id>/...`,
`.../vm/<domain_name>/...`, and `.../host/<metric>`.

## Requirements on the remote host

You set these up yourself per server; the add-on never elevates privileges
on its own:

1. A dedicated service account (no root), added to the group(s) for whichever
   feature(s) you enable on that server:
   ```
   useradd -m -s /usr/sbin/nologin svc_ha_docker
   usermod -aG docker svc_ha_docker      # needed for docker_enabled
   usermod -aG libvirt svc_ha_docker     # needed for libvirt_enabled
   ```
   The account must log back in (or the SSH connection must be fresh) after
   a group change for it to take effect. For libvirt, `virsh`/`libvirt-clients`
   must also be installed and `libvirtd` running on the remote host — the
   add-on talks to it by running `virsh` over the same SSH connection, not a
   separate protocol, so no extra port or socket forwarding is needed for it.
2. SSH key auth for that account — pick one per server:
   - **Generate in the add-on**: leave that server's `key_mode` as
     `generate`, start the add-on, and copy the public key it logs (look
     for the log line naming that server's host) into
     `svc_ha_docker`'s `~/.ssh/authorized_keys` on that host.
   - **Paste an existing key**: set that server's `key_mode` to `paste` and
     put the private key text into its `private_key` field. The add-on's
     Configuration tab renders that as a single-line box in its basic form
     — switch to **Edit in YAML** (top-right of the tab) to paste the full
     multi-line PEM text comfortably, and use a literal block scalar so
     YAML keeps the line breaks intact:
     ```yaml
     servers:
       - host: "192.168.1.101"
         private_key: |
           -----BEGIN OPENSSH PRIVATE KEY-----
           ...
           -----END OPENSSH PRIVATE KEY-----
     ```
     Without the `|`, a plain multi-line YAML value gets its line breaks
     folded into spaces, which corrupts the key and fails with a
     `Missing PEM footer` error. The add-on validates each server's key on
     startup and will tell you this, naming which server, if it happens.

   There is no `upload` mode: Home Assistant's add-on configuration screen
   has no file-picker field type, so a real "choose file" upload isn't
   possible from here — paste is the only way to bring your own key. Each
   server's key (generated or pasted) is stored separately under
   `/data/ssh/<slug of that server's host>/` — servers never share a key.

## Configuration

Every field below except `mqtt.*` lives inside one entry of the `servers`
list — repeat the whole set for each additional server via the **Add**
button.

| Option | Description |
|---|---|
| `servers[].host` / `servers[].port` / `servers[].username` | Remote connection details for this server |
| `servers[].key_mode` | `generate` or `paste` |
| `servers[].private_key` | PEM private key text, only used when `key_mode: paste` |
| `servers[].private_key_passphrase` | Optional passphrase for the private key |
| `servers[].docker_enabled` | Discover/control Docker containers on this server. Default `true`. |
| `servers[].libvirt_enabled` | Discover/control libvirt VMs on this server. Default `false`. |
| `servers[].libvirt_connect_uri` | libvirt connection URI used on this server, default `qemu:///system` |
| `servers[].monitoring_enabled` | Publish this server's disk/memory usage sensors. Default `false`. |
| `servers[].monitoring_disk_paths` | Comma-separated paths on this server whose filesystem usage is reported, e.g. `/, /mnt/data`. Default `/`. A plain string, not a list — see note below. |
| `servers[].poll_interval` | Seconds between this server's libvirt VM state polls and host monitoring updates (Docker uses the Docker event stream instead, no polling) |
| `mqtt.broker_mode` | `homeassistant` (default, uses the Mosquitto add-on) or `external` (a separate broker) — shared by all servers |
| `mqtt.host` / `mqtt.port` / `mqtt.username` / `mqtt.password` | Required in `broker_mode: external`; optional overrides in `broker_mode: homeassistant` (see below) — shared by all servers |
| `mqtt.discovery_prefix` | HA MQTT discovery prefix, default `homeassistant` — shared by all servers |

`monitoring_disk_paths` is a comma-separated string, not its own nested
list. A list nested inside each `servers[]` entry fails Home Assistant's
add-on schema validation with `Invalid list for option
'monitoring_disk_paths'`, even though the `servers` list itself (a list of
flat objects) is valid — Supervisor's schema validator doesn't support a
list nested inside a list-of-objects entry. This was tried and confirmed to
fail against a live Supervisor instance, so the comma-separated string is
the actual, working shape, not a fallback.

### Host monitoring

`monitoring_enabled` adds three Memory sensors (Used, Available, Use%) plus
three Disk sensors (Used, Free, Use%) *per path* in that server's
`monitoring_disk_paths` — so `"/, /mnt/data"` gives you two full sets of
disk sensors, named e.g. `Host: Disk Used (/)` and
`Host: Disk Used (/mnt/data)`. Each path is queried independently (not
`df /path1 /path2` in one call), since `df` de-duplicates rows for paths
that share a filesystem — e.g. `/` and `/home` are often the same
filesystem — which would otherwise make it ambiguous which output row
belongs to which configured path.

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
- Each server's private key lives only in the add-on's `/data` volume,
  under a subdirectory named for that server; keys are never shared between
  servers or exposed to each other.
- Each server's SSH host key is pinned on first connection and verified on
  every connection after that; if you need protection against a compromised
  first connection, verify the host fingerprint out-of-band before enabling
  the add-on for that server.

## Status

Docker support (SSH transport with Docker socket forwarding, MQTT discovery,
event-driven state updates, reconnect/backoff) has been run end-to-end
against a live Home Assistant Supervisor, MQTT broker, and Docker host, with
several real bugs found and fixed along the way (see git history).

libvirt support: the `virsh` output parser and the MQTT
discovery/command dispatch/polling logic are covered by targeted tests
against fakes, but the actual `virsh` invocations over SSH have not been
exercised against a real libvirtd. Worth validating `libvirt_connect_uri`,
group permissions, and the start/shutdown/reboot commands against your setup
before relying on it — if `virsh list --all` output looks different than
expected (e.g. a very old or very new libvirt version changes the table
format), the parser in `libvirt_client.py` is the place to look.

Host monitoring: `/proc/meminfo` and `df -P -B1` parsing in
`host_monitor.py` are covered by tests against realistic sample output
(including the older-kernel fallback when `MemAvailable` isn't reported),
and the GB/percent conversion and sensor publishing are covered against a
fake monitor, but it hasn't been exercised against a real remote host over
SSH yet.

Multi-server support: confirmed working against a live Supervisor instance,
including the `servers` list's **Add** button in the Configuration tab.
Per-server key isolation/reuse (each server gets its own keypair under
`/data/ssh/<slug>`, verified not to collide or regenerate on restart) and
session isolation (one server's connection failures retry independently and
don't affect any other server's session) are also covered by tests.

A nested list inside each `servers` entry for `monitoring_disk_paths` was
tried and confirmed to fail Supervisor's schema validation (`Invalid list
for option 'monitoring_disk_paths'`) - that's why it's a comma-separated
string instead (see the Configuration section above), not a design
preference.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) — it's also shown in the **Changelog** tab
of this add-on's page in Home Assistant.
