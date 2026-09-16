import json
import re

import aiomqtt


def slugify(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_") or "unnamed"


# key, display label, unit, icon
HOST_SENSORS = (
    ("disk_used", "Disk Used", "GB", "mdi:harddisk"),
    ("disk_free", "Disk Free", "GB", "mdi:harddisk"),
    ("disk_use_percent", "Disk Use", "%", "mdi:harddisk"),
    ("memory_used", "Memory Used", "GB", "mdi:memory"),
    ("memory_free", "Memory Available", "GB", "mdi:memory"),
    ("memory_use_percent", "Memory Use", "%", "mdi:memory"),
)


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

    def _resource_topic(self, kind: str, resource_id: str) -> str:
        return f"ssh_docker_manager/{self.host_id}/{kind}/{resource_id}"

    def _availability_topic(self) -> str:
        return f"ssh_docker_manager/{self.host_id}/availability"

    def _device_payload(self) -> dict:
        return {
            "identifiers": [f"ssh_docker_manager_{self.host_id}"],
            "name": f"SSH host {self.host_id}",
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

    async def publish_docker_discovery(self, container_id: str, name: str) -> None:
        base = self._resource_topic("docker", container_id)
        unique_prefix = f"{self.host_id}_docker_{container_id[:12]}"
        display_name = f"Docker: {name}"
        availability_topic = self._availability_topic()
        device = self._device_payload()

        switch_config = {
            "name": display_name,
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
            "name": f"{display_name} Restart",
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
            "name": f"{display_name} Update",
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

    async def publish_vm_discovery(self, domain_name: str) -> None:
        base = self._resource_topic("vm", domain_name)
        unique_prefix = f"{self.host_id}_vm_{slugify(domain_name)}"
        display_name = f"VM: {domain_name}"
        availability_topic = self._availability_topic()
        device = self._device_payload()

        switch_config = {
            "name": display_name,
            "unique_id": f"{unique_prefix}_power",
            "state_topic": f"{base}/state",
            "command_topic": f"{base}/set",
            "payload_on": "start",
            "payload_off": "shutdown",
            "state_on": "running",
            "state_off": "shut off",
            "availability_topic": availability_topic,
            "device": device,
        }
        await self.client.publish(
            f"{self.discovery_prefix}/switch/{unique_prefix}/config",
            json.dumps(switch_config),
            retain=True,
        )

        reboot_config = {
            "name": f"{display_name} Reboot",
            "unique_id": f"{unique_prefix}_reboot",
            "command_topic": f"{base}/reboot",
            "availability_topic": availability_topic,
            "device": device,
        }
        await self.client.publish(
            f"{self.discovery_prefix}/button/{unique_prefix}_reboot/config",
            json.dumps(reboot_config),
            retain=True,
        )

    async def publish_host_discovery(self) -> None:
        availability_topic = self._availability_topic()
        device = self._device_payload()
        base = f"ssh_docker_manager/{self.host_id}/host"

        for key, label, unit, icon in HOST_SENSORS:
            unique_id = f"{self.host_id}_host_{key}"
            config = {
                "name": f"Host: {label}",
                "unique_id": unique_id,
                "state_topic": f"{base}/{key}",
                "unit_of_measurement": unit,
                "state_class": "measurement",
                "icon": icon,
                "availability_topic": availability_topic,
                "device": device,
            }
            await self.client.publish(
                f"{self.discovery_prefix}/sensor/{unique_id}/config",
                json.dumps(config),
                retain=True,
            )

    async def publish_host_state(self, key: str, value) -> None:
        await self.client.publish(
            f"ssh_docker_manager/{self.host_id}/host/{key}", str(value), retain=True
        )

    async def publish_state(self, kind: str, resource_id: str, state: str) -> None:
        await self.client.publish(
            f"{self._resource_topic(kind, resource_id)}/state", state, retain=True
        )

    async def publish_availability(self, online: bool) -> None:
        await self.client.publish(
            self._availability_topic(), "online" if online else "offline", retain=True
        )

    async def subscribe_commands(self) -> None:
        # ssh_docker_manager/<host_id>/<kind>/<resource_id>/<action>
        await self.client.subscribe(f"ssh_docker_manager/{self.host_id}/+/+/+")
