import json

import aiomqtt


class MqttBridge:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        discovery_prefix: str,
        host_id: str,
    ):
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self.discovery_prefix = discovery_prefix
        self.host_id = host_id
        self.client: aiomqtt.Client | None = None

    def _base_topic(self, container_id: str) -> str:
        return f"ssh_docker_manager/{self.host_id}/{container_id}"

    def _availability_topic(self) -> str:
        return f"ssh_docker_manager/{self.host_id}/availability"

    def _device_payload(self) -> dict:
        return {
            "identifiers": [f"ssh_docker_manager_{self.host_id}"],
            "name": f"Docker host {self.host_id}",
            "manufacturer": "ssh_docker_manager",
        }

    async def connect(self) -> None:
        self.client = aiomqtt.Client(
            hostname=self._host,
            port=self._port,
            username=self._username or None,
            password=self._password or None,
        )
        await self.client.__aenter__()

    async def disconnect(self) -> None:
        if self.client:
            await self.client.__aexit__(None, None, None)

    async def publish_discovery(self, container_id: str, name: str) -> None:
        base = self._base_topic(container_id)
        unique_prefix = f"{self.host_id}_{container_id[:12]}"
        availability_topic = self._availability_topic()
        device = self._device_payload()

        switch_config = {
            "name": name,
            "unique_id": f"{unique_prefix}_power",
            "state_topic": f"{base}/state",
            "command_topic": f"{base}/set",
            "payload_on": "start",
            "payload_off": "stop",
            "state_on": "running",
            "state_off": "exited",
            "availability_topic": availability_topic,
            "device": device,
        }
        await self.client.publish(
            f"{self.discovery_prefix}/switch/{unique_prefix}/config",
            json.dumps(switch_config),
            retain=True,
        )

        restart_config = {
            "name": f"{name} Restart",
            "unique_id": f"{unique_prefix}_restart",
            "command_topic": f"{base}/restart",
            "availability_topic": availability_topic,
            "device": device,
        }
        await self.client.publish(
            f"{self.discovery_prefix}/button/{unique_prefix}_restart/config",
            json.dumps(restart_config),
            retain=True,
        )

        update_config = {
            "name": f"{name} Update",
            "unique_id": f"{unique_prefix}_update",
            "command_topic": f"{base}/update",
            "latest_version_topic": f"{base}/update/latest",
            "installed_version_topic": f"{base}/update/installed",
            "availability_topic": availability_topic,
            "device": device,
        }
        await self.client.publish(
            f"{self.discovery_prefix}/update/{unique_prefix}_update/config",
            json.dumps(update_config),
            retain=True,
        )

    async def publish_state(self, container_id: str, state: str) -> None:
        await self.client.publish(f"{self._base_topic(container_id)}/state", state, retain=True)

    async def publish_availability(self, online: bool) -> None:
        await self.client.publish(
            self._availability_topic(), "online" if online else "offline", retain=True
        )

    async def subscribe_commands(self) -> None:
        await self.client.subscribe(f"ssh_docker_manager/{self.host_id}/+/set")
        await self.client.subscribe(f"ssh_docker_manager/{self.host_id}/+/restart")
        await self.client.subscribe(f"ssh_docker_manager/{self.host_id}/+/update")
