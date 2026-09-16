import json
import re

import aiomqtt


def slugify(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_") or "unnamed"


# key, display label, unit, icon
MEMORY_SENSORS = (
    ("memory_used", "Memory Used", "GB", "mdi:memory"),
    ("memory_free", "Memory Available", "GB", "mdi:memory"),
    ("memory_use_percent", "Memory Use", "%", "mdi:memory"),
)

# metric suffix, display label, unit, icon
DISK_METRICS = (
    ("used", "Disk Used", "GB", "mdi:harddisk"),
    ("free", "Disk Free", "GB", "mdi:harddisk"),
    ("use_percent", "Disk Use", "%", "mdi:harddisk"),
)


def slugify_path(path: str) -> str:
    # slugify("/") collapses to nothing since every character is stripped;
    # special-case the (very common) root path to a readable "root" instead
    # of falling through to slugify's generic "unnamed".
    if path.strip("/") == "":
        return "root"
    return slugify(path)


def disk_metric_key(path: str, metric: str) -> str:
    return f"disk_{metric}_{slugify_path(path)}"


class MqttBridge:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        discovery_prefix: str,
        ssh_host: str,
    ):
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self.discovery_prefix = discovery_prefix
        self.host_slug = slugify(ssh_host)
        self.topic_root = f"ssh_manage_{self.host_slug}"
        self._display_host = ssh_host
        self.client: aiomqtt.Client | None = None

    def _resource_topic(self, kind: str, resource_id: str) -> str:
        return f"{self.topic_root}/{kind}/{resource_id}"

    def _availability_topic(self) -> str:
        return f"{self.topic_root}/availability"

    def _device_payload(self) -> dict:
        return {
            "identifiers": [self.topic_root],
            "name": f"SSH host {self._display_host}",
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
        unique_prefix = f"{self.host_slug}_docker_{container_id[:12]}"
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
        unique_prefix = f"{self.host_slug}_vm_{slugify(domain_name)}"
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

    async def publish_host_discovery(self, disk_paths: list[str]) -> None:
        availability_topic = self._availability_topic()
        device = self._device_payload()
        base = f"{self.topic_root}/host"

        for key, label, unit, icon in MEMORY_SENSORS:
            await self._publish_host_sensor_config(base, key, label, unit, icon)

        for path in disk_paths:
            for metric, label, unit, icon in DISK_METRICS:
                key = disk_metric_key(path, metric)
                await self._publish_host_sensor_config(
                    base, key, f"{label} ({path})", unit, icon
                )

    async def _publish_host_sensor_config(
        self, base: str, key: str, label: str, unit: str, icon: str
    ) -> None:
        unique_id = f"{self.host_slug}_host_{key}"
        config = {
            "name": f"Host: {label}",
            "unique_id": unique_id,
            "state_topic": f"{base}/{key}",
            "unit_of_measurement": unit,
            "state_class": "measurement",
            "icon": icon,
            "availability_topic": self._availability_topic(),
            "device": self._device_payload(),
        }
        await self.client.publish(
            f"{self.discovery_prefix}/sensor/{unique_id}/config",
            json.dumps(config),
            retain=True,
        )

    async def publish_host_state(self, key: str, value) -> None:
        await self.client.publish(f"{self.topic_root}/host/{key}", str(value), retain=True)

    async def publish_state(self, kind: str, resource_id: str, state: str) -> None:
        await self.client.publish(
            f"{self._resource_topic(kind, resource_id)}/state", state, retain=True
        )

    async def publish_availability(self, online: bool) -> None:
        await self.client.publish(
            self._availability_topic(), "online" if online else "offline", retain=True
        )

    async def subscribe_commands(self) -> None:
        # ssh_manage_<hostname>/<kind>/<resource_id>/<action>
        await self.client.subscribe(f"{self.topic_root}/+/+/+")
