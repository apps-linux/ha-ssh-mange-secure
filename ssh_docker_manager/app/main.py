import asyncio
import hashlib
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import config
import key_manager
import ssh_client
from docker_client import DockerManager
from mqtt_client import MqttBridge

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ssh_docker_manager")

RECONNECT_BACKOFF = [2, 5, 10, 30, 60]


def host_id_for(host: str) -> str:
    return hashlib.sha1(host.encode()).hexdigest()[:8]


async def run_session(options: dict) -> None:
    ssh_opts = options["ssh"]
    mqtt_conf = config.resolve_mqtt(options)

    key_mode = ssh_opts.get("key_mode", "generate")
    key_path = key_manager.ensure_key(
        key_mode,
        ssh_opts.get("private_key"),
        ssh_opts.get("private_key_file"),
        ssh_opts.get("private_key_passphrase"),
    )

    if key_mode == "generate":
        pub = key_manager.public_key_line()
        if pub:
            log.info("Add this public key to the remote account's authorized_keys:\n%s", pub)

    host_id = host_id_for(ssh_opts["host"])
    mqtt = MqttBridge(
        mqtt_conf["host"],
        mqtt_conf["port"],
        mqtt_conf["username"],
        mqtt_conf["password"],
        options["mqtt"].get("discovery_prefix", "homeassistant"),
        host_id,
    )
    await mqtt.connect()

    conn = None
    docker_mgr = None
    try:
        conn = await ssh_client.connect(
            ssh_opts["host"],
            ssh_opts.get("port", 22),
            ssh_opts["username"],
            [key_path],
            ssh_opts.get("private_key_passphrase"),
        )

        version = await ssh_client.verify_docker_access(conn)
        log.info("Connected. Remote Docker Engine version: %s", version)

        _, sock_path = await ssh_client.forward_docker_socket(conn)
        docker_mgr = DockerManager(sock_path)

        await mqtt.publish_availability(True)

        for c in await docker_mgr.list_containers():
            info = await c.show()
            name = info["Name"].lstrip("/")
            await mqtt.publish_discovery(c.id, name)
            await mqtt.publish_state(c.id, info["State"]["Status"])

        await mqtt.subscribe_commands()

        command_task = asyncio.create_task(_handle_commands(mqtt, docker_mgr))
        events_task = asyncio.create_task(_handle_events(mqtt, docker_mgr))
        closed_task = asyncio.create_task(conn.wait_closed())

        done, pending = await asyncio.wait(
            [command_task, events_task, closed_task], return_when=asyncio.FIRST_COMPLETED
        )
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


async def _handle_events(mqtt: MqttBridge, docker_mgr: DockerManager) -> None:
    async for event in docker_mgr.events():
        if event.get("Type") != "container":
            continue
        container_id = event.get("id") or event.get("Actor", {}).get("ID")
        status = event.get("status")
        if not container_id or not status:
            continue
        await mqtt.publish_state(container_id, status)


async def _handle_commands(mqtt: MqttBridge, docker_mgr: DockerManager) -> None:
    async for message in mqtt.client.messages:
        parts = str(message.topic).split("/")
        # ssh_docker_manager/<host_id>/<container_id>/<action>
        if len(parts) != 4:
            continue
        _, _, container_id, action = parts
        payload = message.payload.decode() if isinstance(message.payload, bytes) else message.payload

        try:
            if action == "set":
                if payload == "start":
                    await docker_mgr.start(container_id)
                elif payload == "stop":
                    await docker_mgr.stop(container_id)
            elif action == "restart":
                await docker_mgr.restart(container_id)
            elif action == "update":
                await docker_mgr.update_image(container_id)
        except Exception:
            log.exception("Command '%s' failed for container %s", action, container_id)


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
