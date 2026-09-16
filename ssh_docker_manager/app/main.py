import asyncio
import logging
import os
import sys

import aiomqtt

sys.path.insert(0, os.path.dirname(__file__))

import config
import key_manager
import ssh_client
from docker_client import DockerManager
from host_monitor import HostMonitor, bytes_to_gb, percent
from libvirt_client import LibvirtManager
from mqtt_client import MqttBridge, disk_metric_key

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ssh_docker_manager")

RECONNECT_BACKOFF = [2, 5, 10, 30, 60]


async def run_session(options: dict) -> None:
    ssh_opts = options["ssh"]
    mqtt_conf = config.resolve_mqtt(options)

    docker_enabled = options.get("docker", {}).get("enabled", True)
    libvirt_opts = options.get("libvirt", {})
    libvirt_enabled = libvirt_opts.get("enabled", False)
    libvirt_uri = libvirt_opts.get("connect_uri") or "qemu:///system"
    monitoring_opts = options.get("monitoring", {})
    monitoring_enabled = monitoring_opts.get("enabled", False)
    disk_paths = monitoring_opts.get("disk_paths") or ["/"]
    poll_interval = options.get("poll_interval", 30)

    if not docker_enabled and not libvirt_enabled and not monitoring_enabled:
        raise RuntimeError(
            "docker.enabled, libvirt.enabled and monitoring.enabled are all false - "
            "enable at least one."
        )

    key_mode = ssh_opts.get("key_mode", "generate")
    key_path = key_manager.ensure_key(
        key_mode, ssh_opts.get("private_key"), ssh_opts.get("private_key_passphrase")
    )

    if key_mode == "generate":
        pub = key_manager.public_key_line()
        if pub:
            log.info("Add this public key to the remote account's authorized_keys:\n%s", pub)

    mqtt = MqttBridge(
        mqtt_conf["host"],
        mqtt_conf["port"],
        mqtt_conf["username"],
        mqtt_conf["password"],
        options["mqtt"].get("discovery_prefix", "homeassistant"),
        ssh_opts["host"],
    )
    try:
        await mqtt.connect()
    except aiomqtt.MqttError as exc:
        raise RuntimeError(
            f"Could not connect to MQTT broker {mqtt_conf['host']}:{mqtt_conf['port']} "
            f"({exc}). If this is 'Not authorized', the broker is rejecting the "
            "configured mqtt.username/mqtt.password (or you're connecting anonymously "
            "to a broker that requires a login) - add/fix a login for this add-on in "
            "the Mosquitto add-on's configuration and match it in mqtt.username/mqtt.password."
        ) from exc

    conn = None
    docker_mgr = None
    libvirt_mgr = None
    try:
        conn = await ssh_client.connect(
            ssh_opts["host"],
            ssh_opts.get("port", 22),
            ssh_opts["username"],
            [key_path],
            ssh_opts.get("private_key_passphrase"),
        )

        if docker_enabled:
            version = await ssh_client.verify_docker_access(conn)
            log.info("Connected. Remote Docker Engine version: %s", version)

            _, sock_path = await ssh_client.forward_docker_socket(conn)
            docker_mgr = DockerManager(sock_path)

        if libvirt_enabled:
            libvirt_mgr = LibvirtManager(conn, libvirt_uri)
            await libvirt_mgr.verify_access()
            log.info("Connected. Verified libvirt access via %s", libvirt_uri)

        host_monitor = None
        if monitoring_enabled:
            host_monitor = HostMonitor(conn, disk_paths)

        await mqtt.publish_availability(True)

        if docker_mgr:
            for c in await docker_mgr.list_containers():
                info = await c.show()
                name = info["Name"].lstrip("/")
                await mqtt.publish_docker_discovery(c.id, name)
                await mqtt.publish_state("docker", c.id, info["State"]["Status"])

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

        await mqtt.subscribe_commands()

        tasks = [
            asyncio.create_task(_handle_commands(mqtt, docker_mgr, libvirt_mgr)),
            asyncio.create_task(conn.wait_closed()),
        ]
        if docker_mgr:
            tasks.append(asyncio.create_task(_handle_docker_events(mqtt, docker_mgr)))
        if libvirt_mgr:
            tasks.append(
                asyncio.create_task(
                    _poll_libvirt(mqtt, libvirt_mgr, poll_interval, known_vms, vm_state)
                )
            )
        if host_monitor:
            tasks.append(asyncio.create_task(_poll_host(mqtt, host_monitor, poll_interval)))

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


async def _handle_docker_events(mqtt: MqttBridge, docker_mgr: DockerManager) -> None:
    async for event in docker_mgr.events():
        if event.get("Type") != "container":
            continue
        container_id = event.get("id") or event.get("Actor", {}).get("ID")
        status = event.get("status")
        if not container_id or not status:
            continue
        await mqtt.publish_state("docker", container_id, status)


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
    while True:
        await asyncio.sleep(poll_interval)
        await _publish_host_metrics(mqtt, host_monitor)


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


async def main() -> None:
    options = config.load_options()
    backoff_idx = 0
    while True:
        try:
            await run_session(options)
            backoff_idx = 0
        except Exception:
            log.exception("Session ended with an error, reconnecting")
        delay = RECONNECT_BACKOFF[min(backoff_idx, len(RECONNECT_BACKOFF) - 1)]
        backoff_idx += 1
        await asyncio.sleep(delay)


if __name__ == "__main__":
    asyncio.run(main())
