import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import aiomqtt

sys.path.insert(0, os.path.dirname(__file__))

import config
import key_manager
import ssh_client
from backup_status import BackupStatusReader, HEALTHY_JOB_STATES, job_attributes, job_state, summarize
from docker_client import DockerManager
from host_monitor import HostMonitor, bytes_to_gb, cpu_percent, percent
from libvirt_client import LibvirtManager
from paperless_status import PaperlessStatusReader, uploads_state
from mqtt_client import MqttBridge, disk_metric_key
from util import parse_disk_paths, slugify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ssh_docker_manager")

RECONNECT_BACKOFF = [2, 5, 10, 30, 60]


async def run_server_session(server: dict, mqtt_conf: dict, discovery_prefix: str) -> None:
    host = server["host"]
    server_slug = slugify(host)

    docker_enabled = server.get("docker_enabled", True)
    libvirt_enabled = server.get("libvirt_enabled", False)
    libvirt_uri = server.get("libvirt_connect_uri") or "qemu:///system"
    monitoring_enabled = server.get("monitoring_enabled", False)
    disk_paths = parse_disk_paths(server.get("monitoring_disk_paths"))
    poll_interval = server.get("poll_interval", 30)
    backup_status_path = (server.get("backup_status_path") or "").strip()
    backup_max_age = timedelta(hours=server.get("backup_max_age_hours") or 26)
    paperless_status_path = (server.get("paperless_status_path") or "").strip()

    if (
        not docker_enabled
        and not libvirt_enabled
        and not monitoring_enabled
        and not backup_status_path
        and not paperless_status_path
    ):
        raise RuntimeError(
            f"Server '{host}': docker_enabled, libvirt_enabled and monitoring_enabled "
            "are all false and backup_status_path and paperless_status_path are empty - "
            "enable at least one."
        )

    key_mode = server.get("key_mode", "generate")
    key_path = key_manager.ensure_key(
        server_slug, key_mode, server.get("private_key"), server.get("private_key_passphrase")
    )

    if key_mode == "generate":
        pub = key_manager.public_key_line(server_slug)
        if pub:
            log.info(
                "[%s] Add this public key to the remote account's authorized_keys:\n%s",
                host,
                pub,
            )

    mqtt = MqttBridge(
        mqtt_conf["host"],
        mqtt_conf["port"],
        mqtt_conf["username"],
        mqtt_conf["password"],
        discovery_prefix,
        host,
    )
    try:
        await mqtt.connect()
    except aiomqtt.MqttError as exc:
        raise RuntimeError(
            f"[{host}] Could not connect to MQTT broker {mqtt_conf['host']}:{mqtt_conf['port']} "
            f"({exc}). If this is 'Not authorized', the broker is rejecting the configured "
            "MQTT username/password (or you're connecting anonymously to a broker that "
            "requires a login) - add/fix a login for this add-on in the Mosquitto add-on's "
            "configuration and match it in the mqtt options."
        ) from exc

    conn = None
    docker_mgr = None
    libvirt_mgr = None
    try:
        conn = await ssh_client.connect(
            host,
            server.get("port", 22),
            server["username"],
            [key_path],
            server.get("private_key_passphrase"),
        )

        if docker_enabled:
            version = await ssh_client.verify_docker_access(conn)
            log.info("[%s] Connected. Remote Docker Engine version: %s", host, version)

            _, sock_path = await ssh_client.forward_docker_socket(conn)
            docker_mgr = DockerManager(sock_path)

        if libvirt_enabled:
            libvirt_mgr = LibvirtManager(conn, libvirt_uri)
            await libvirt_mgr.verify_access()
            log.info("[%s] Connected. Verified libvirt access via %s", host, libvirt_uri)

        host_monitor = None
        if monitoring_enabled:
            host_monitor = HostMonitor(conn, disk_paths)

        backup_reader = None
        if backup_status_path:
            backup_reader = BackupStatusReader(conn, backup_status_path)

        paperless_reader = None
        if paperless_status_path:
            paperless_reader = PaperlessStatusReader(conn, paperless_status_path)

        await mqtt.publish_availability(True)

        known_containers: set[str] = set()
        if docker_mgr:
            containers = {}
            for c in await docker_mgr.list_containers():
                info = await c.show()
                containers[info["Name"].lstrip("/")] = info["State"]["Status"]
            stale = await mqtt.prune_docker_discovery(list(containers))
            if stale:
                log.info("[%s] Removed %d stale Docker discovery configs", host, len(stale))
                # Let HA drop the old entities first, so the new ones get the
                # plain entity_ids instead of a _2 suffix.
                await asyncio.sleep(2)
            for name, status in containers.items():
                await mqtt.publish_docker_discovery(name)
                await mqtt.publish_state("docker", name, status)
                known_containers.add(name)

        known_vms: set[str] = set()
        vm_state: dict[str, str] = {}
        if libvirt_mgr:
            for domain in await libvirt_mgr.list_domains():
                name = domain["name"]
                await mqtt.publish_vm_discovery(name)
                await mqtt.publish_state("vm", name, domain["state"])
                known_vms.add(name)
                vm_state[name] = domain["state"]

        if host_monitor:
            await mqtt.publish_host_discovery(disk_paths)
            await _publish_host_metrics(mqtt, host_monitor)

        known_backup_jobs: set[str] = set()
        if backup_reader:
            await mqtt.publish_backup_summary_discovery()
            await _publish_backup_status(
                mqtt, backup_reader, backup_max_age, known_backup_jobs, host
            )

        if paperless_reader:
            await mqtt.publish_paperless_discovery()
            await _publish_paperless_status(mqtt, paperless_reader, host)

        await mqtt.subscribe_commands()

        tasks = [
            asyncio.create_task(_handle_commands(mqtt, docker_mgr, libvirt_mgr)),
            asyncio.create_task(conn.wait_closed()),
        ]
        if docker_mgr:
            tasks.append(
                asyncio.create_task(_handle_docker_events(mqtt, docker_mgr, known_containers))
            )
        if libvirt_mgr:
            tasks.append(
                asyncio.create_task(
                    _poll_libvirt(mqtt, libvirt_mgr, poll_interval, known_vms, vm_state)
                )
            )
        if host_monitor:
            tasks.append(asyncio.create_task(_poll_host(mqtt, host_monitor, poll_interval)))
        if backup_reader:
            tasks.append(
                asyncio.create_task(
                    _poll_backups(
                        mqtt, backup_reader, poll_interval, backup_max_age, known_backup_jobs, host
                    )
                )
            )
        if paperless_reader:
            tasks.append(
                asyncio.create_task(_poll_paperless(mqtt, paperless_reader, poll_interval, host))
            )

        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc:
                raise exc
    finally:
        await mqtt.publish_availability(False)
        if docker_mgr:
            await docker_mgr.close()
        if conn:
            conn.close()
        await mqtt.disconnect()


# Container events that change State.Status (exec_*/health events don't).
STATE_EVENTS = {"start", "die", "stop", "pause", "unpause"}


async def _handle_docker_events(
    mqtt: MqttBridge, docker_mgr: DockerManager, known_containers: set[str]
) -> None:
    async for event in docker_mgr.events():
        if event.get("Type") != "container" or event.get("Action") not in STATE_EVENTS:
            continue
        name = event.get("Actor", {}).get("Attributes", {}).get("name")
        if not name:
            continue
        # The event's own status ("start", "die", ...) isn't a container state;
        # publish the real one ("running", "exited", ...) the switch expects.
        status = await docker_mgr.status(name)
        if status is None:
            continue
        if name not in known_containers:
            await mqtt.publish_docker_discovery(name)
            known_containers.add(name)
        await mqtt.publish_state("docker", name, status)


async def _poll_libvirt(
    mqtt: MqttBridge,
    libvirt_mgr: LibvirtManager,
    poll_interval: int,
    known_vms: set[str],
    vm_state: dict[str, str],
) -> None:
    while True:
        await asyncio.sleep(poll_interval)

        domains = await libvirt_mgr.list_domains()
        seen = set()
        for domain in domains:
            name = domain["name"]
            state = domain["state"]
            seen.add(name)

            if name not in known_vms:
                await mqtt.publish_vm_discovery(name)
                known_vms.add(name)

            if vm_state.get(name) != state:
                await mqtt.publish_state("vm", name, state)
                vm_state[name] = state

        for removed in known_vms - seen:
            vm_state.pop(removed, None)
        known_vms.intersection_update(seen)


async def _publish_host_metrics(mqtt: MqttBridge, host_monitor: HostMonitor) -> None:
    memory = await host_monitor.get_memory()
    await mqtt.publish_host_state("memory_used", bytes_to_gb(memory["used"]))
    await mqtt.publish_host_state("memory_free", bytes_to_gb(memory["available"]))
    await mqtt.publish_host_state(
        "memory_use_percent", percent(memory["used"], memory["total"])
    )

    disks = await host_monitor.get_disks()
    for path, disk in disks.items():
        await mqtt.publish_host_state(disk_metric_key(path, "used"), bytes_to_gb(disk["used"]))
        await mqtt.publish_host_state(
            disk_metric_key(path, "free"), bytes_to_gb(disk["available"])
        )
        await mqtt.publish_host_state(
            disk_metric_key(path, "use_percent"), percent(disk["used"], disk["total"])
        )


async def _poll_host(mqtt: MqttBridge, host_monitor: HostMonitor, poll_interval: int) -> None:
    # CPU usage needs two /proc/stat samples to compute a delta (the same
    # technique top/htop use), so the first sample is taken here and the
    # first CPU reading is published after the first poll_interval tick,
    # one cycle later than the disk/memory sensors that publish immediately
    # at startup.
    prev_cpu = await host_monitor.get_cpu_stat()

    while True:
        await asyncio.sleep(poll_interval)
        await _publish_host_metrics(mqtt, host_monitor)

        curr_cpu = await host_monitor.get_cpu_stat()
        usage = cpu_percent(prev_cpu, curr_cpu)
        if usage is not None:
            await mqtt.publish_host_state("cpu_use_percent", usage)
        prev_cpu = curr_cpu


async def _publish_backup_status(
    mqtt: MqttBridge,
    reader: BackupStatusReader,
    max_age: timedelta,
    known_jobs: set[str],
    host: str,
) -> None:
    now = datetime.now(timezone.utc)
    try:
        status = await reader.read()
    except Exception as exc:
        # A missing or broken status file is a backup problem to show in HA,
        # not a reason to tear down this server's SSH session.
        log.warning("[%s] Backup status unavailable: %s", host, exc)
        await mqtt.publish_backup_summary_state(
            "error", True, None, {"problems": [str(exc)]}
        )
        return

    for job in status["jobs"]:
        name = job["name"]
        if name not in known_jobs:
            await mqtt.publish_backup_job_discovery(name)
            known_jobs.add(name)
        state = job_state(job, now, max_age)
        await mqtt.publish_backup_job_state(
            name,
            state,
            state not in HEALTHY_JOB_STATES,
            job.get("latest_snapshot_time"),
            job_attributes(job),
        )

    summary = summarize(status, now, max_age)
    await mqtt.publish_backup_summary_state(
        summary["state"],
        summary["state"] != "ok",
        status.get("generated"),
        summary["attributes"],
    )


async def _poll_backups(
    mqtt: MqttBridge,
    reader: BackupStatusReader,
    poll_interval: int,
    max_age: timedelta,
    known_jobs: set[str],
    host: str,
) -> None:
    while True:
        await asyncio.sleep(poll_interval)
        await _publish_backup_status(mqtt, reader, max_age, known_jobs, host)


async def _publish_paperless_status(
    mqtt: MqttBridge, reader: PaperlessStatusReader, host: str
) -> None:
    try:
        data = await reader.read()
    except Exception as exc:
        # Like backups: a missing/broken file shows in HA, the session stays up.
        log.warning("[%s] Paperless status unavailable: %s", host, exc)
        await mqtt.publish_paperless_state(None, {"error": str(exc), "documents": []})
        return
    last_upload, attributes = uploads_state(data)
    await mqtt.publish_paperless_state(last_upload, attributes)


async def _poll_paperless(
    mqtt: MqttBridge, reader: PaperlessStatusReader, poll_interval: int, host: str
) -> None:
    while True:
        await asyncio.sleep(poll_interval)
        await _publish_paperless_status(mqtt, reader, host)


async def _handle_commands(
    mqtt: MqttBridge, docker_mgr: DockerManager | None, libvirt_mgr: LibvirtManager | None
) -> None:
    async for message in mqtt.client.messages:
        parts = str(message.topic).split("/")
        # ssh_manage_<hostname>/<kind>/<resource_id>/<action>
        if len(parts) != 4:
            continue
        _, kind, resource_id, action = parts
        payload = message.payload.decode() if isinstance(message.payload, bytes) else message.payload

        try:
            if kind == "docker" and docker_mgr:
                if action == "set":
                    if payload == "start":
                        await docker_mgr.start(resource_id)
                    elif payload == "stop":
                        await docker_mgr.stop(resource_id)
                elif action == "restart":
                    await docker_mgr.restart(resource_id)
                elif action == "update":
                    await docker_mgr.update_image(resource_id)
            elif kind == "vm" and libvirt_mgr:
                if action == "set":
                    if payload == "start":
                        await libvirt_mgr.start(resource_id)
                    elif payload == "shutdown":
                        await libvirt_mgr.shutdown(resource_id)
                elif action == "reboot":
                    await libvirt_mgr.reboot(resource_id)
        except Exception:
            log.exception("Command '%s' failed for %s %s", action, kind, resource_id)


async def _server_supervisor(server: dict, mqtt_conf: dict, discovery_prefix: str) -> None:
    host = server.get("host", "?")
    backoff_idx = 0
    while True:
        try:
            await run_server_session(server, mqtt_conf, discovery_prefix)
            backoff_idx = 0
        except Exception:
            log.exception("[%s] Session ended with an error, reconnecting", host)
        delay = RECONNECT_BACKOFF[min(backoff_idx, len(RECONNECT_BACKOFF) - 1)]
        backoff_idx += 1
        await asyncio.sleep(delay)


async def main() -> None:
    while True:
        try:
            options = config.load_options()
            servers = options.get("servers") or []
            mqtt_conf = config.resolve_mqtt(options)
            discovery_prefix = options.get("mqtt", {}).get("discovery_prefix", "homeassistant")

            if not servers:
                log.error(
                    "No servers configured - add at least one entry under 'servers' in "
                    "the add-on configuration."
                )
                await asyncio.sleep(3600)
                continue

            # Each server runs its own independent, self-reconnecting session; one
            # server's failure never affects the others.
            await asyncio.gather(
                *(
                    _server_supervisor(server, mqtt_conf, discovery_prefix)
                    for server in servers
                )
            )
        except Exception:
            log.exception("Startup failed, retrying")
            await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(main())
