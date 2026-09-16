# SSH Linux Management

A Home Assistant add-on repository. This add-on connects to a remote Linux
host over SSH and exposes what's running there — Docker containers and
libvirt/QEMU virtual machines — to Home Assistant as MQTT entities, so you
can see and control them from dashboards and automations without giving
Home Assistant, or this add-on, root access to that host.

## Why

Home Assistant has no built-in way to manage a Docker host or a hypervisor
that isn't itself running Home Assistant. The usual workarounds are either
broad (a generic SSH/command-line integration that shells out ad hoc) or
require installing an agent with more privilege than the task needs. This
add-on is narrower on purpose:

- It only ever does what the SSH account you configure is allowed to do.
- That account is never root — it's a normal user added to the `docker`
  and/or `libvirt` groups on the remote host, which is the standard way to
  grant those permissions without full root.
- Docker and libvirt management are independent toggles, so a host running
  only one of the two doesn't need the other enabled.

## What it does

- **Discovers** Docker containers and/or libvirt VMs on the remote host and
  publishes them to Home Assistant via MQTT discovery — no manual entity
  configuration.
- **Controls** them: start, stop, restart containers and update their
  images; start, shut down, and reboot VMs.
- **Tracks state** live — Docker container state comes from the Docker
  event stream (push, not polling); VM state is polled on an interval since
  libvirt has no equivalent stream reachable over plain SSH.
- **Keeps Docker and VM entities distinguishable** — entity names are
  prefixed `Docker:` / `VM:` so the two don't get confused in the Home
  Assistant UI, since both can be enabled on the same host at once.

## How it connects

The add-on opens a single outbound SSH connection to the remote host using
a key pair you either let it generate or supply yourself. From there:

- **Docker**: the remote Docker socket is forwarded over that same SSH
  connection (no extra port, no Docker TCP API exposed), and the add-on
  talks to it directly as a client.
- **libvirt**: the add-on runs `virsh` over the same SSH connection — no
  separate protocol, no `libvirt-python` bindings, nothing extra to expose
  on the remote host beyond having `virsh` installed and `libvirtd`
  running.

The SSH host key is pinned on first connection and verified on every
connection after that, the same way a normal SSH client's `known_hosts`
file works.

## Installation

Add this repository's URL to Home Assistant under
**Settings → Add-ons → Add-on Store → Repositories**, then install
**SSH Linux Management** from the store. Full setup — creating the remote
service account, SSH key options, all configuration fields, and current
implementation status — is documented in the add-on's own README:

- [`ssh_docker_manager/README.md`](ssh_docker_manager/README.md)
