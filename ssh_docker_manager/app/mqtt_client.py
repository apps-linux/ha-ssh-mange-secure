import asyncio
import json

import aiomqtt

from util import slugify, slugify_path

# key, display label, unit, icon
MEMORY_SENSORS = (
    ("memory_used", "Memory Used", "GB", "mdi:memory"),
    ("memory_free", "Memory Available", "GB", "mdi:memory"),
    ("memory_use_percent", "Memory Use", "%", "mdi:memory"),
)

CPU_SENSORS = (("cpu_use_percent", "CPU Use", "%", "mdi:cpu-64-bit"),)

# metric suffix, display label, unit, icon
DISK_METRICS = (
    ("used", "Disk Used", "GB", "mdi:harddisk"),
    ("free", "Disk Free", "GB", "mdi:harddisk"),
    ("use_percent", "Disk Use", "%", "mdi:harddisk"),
)


def disk_metric_key(path: str, metric: str) -> str:
    return f"disk_{metric}_{slugify_path(path)}"


def stale_docker_topics(retained: set[str], keep: set[str], object_prefix: str) -> list[str]:
    """Retained discovery topics <prefix>/<component>/<object_id>/config whose
    object_id belongs to this host's Docker entities but isn't in `keep`."""
    return sorted(
        t for t in retained
        if t not in keep
        and t.endswith("/config")
        and t.split("/")[-2].startswith(object_prefix)
    )


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

    def _docker_unique_prefix(self, name: str) -> str:
        # Keyed on the container name, not its ID: compose/update recreates a
        # container with a new ID but the same name, and an ID-based unique_id
        # left a stale duplicate entity (_2, _3, ...) in HA after every recreate.
        return f"{self.host_slug}_docker_{slugify(name)}"

    def docker_discovery_topics(self, name: str) -> list[str]:
        prefix = self._docker_unique_prefix(name)
        return [
            f"{self.discovery_prefix}/switch/{prefix}/config",
            f"{self.discovery_prefix}/button/{prefix}_restart/config",
            f"{self.discovery_prefix}/update/{prefix}_update/config",
        ]

    async def prune_docker_discovery(self, keep_names: list[str]) -> list[str]:
        """Clears retained Docker discovery configs of this host that don't
        belong to a current container (removed containers, and configs from
        the old ID-based scheme), so HA removes those entities for good."""
        topics = [
            f"{self.discovery_prefix}/{component}/+/config"
            for component in ("switch", "button", "update")
        ]
        for topic in topics:
            await self.client.subscribe(topic)
        retained = await self._collect_retained()
        for topic in topics:
            await self.client.unsubscribe(topic)

        keep = {t for name in keep_names for t in self.docker_discovery_topics(name)}
        stale = stale_docker_topics(retained, keep, f"{self.host_slug}_docker_")
        for topic in stale:
            await self.client.publish(topic, b"", retain=True)
        return stale

    async def _collect_retained(self, quiet: float = 1.0, limit: float = 10.0) -> set[str]:
        # Retained messages arrive right after subscribing; stop once the
        # broker has been quiet for `quiet` seconds.
        topics: set[str] = set()
        messages = aiter(self.client.messages)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + limit
        while loop.time() < deadline:
            try:
                message = await asyncio.wait_for(anext(messages), quiet)
            except asyncio.TimeoutError:
                break
            if message.retain and message.payload:
                topics.add(str(message.topic))
        return topics

    async def publish_docker_discovery(self, name: str) -> None:
        base = self._resource_topic("docker", name)
        unique_prefix = self._docker_unique_prefix(name)
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

        for key, label, unit, icon in MEMORY_SENSORS + CPU_SENSORS:
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

    async def _publish_discovery(self, component: str, unique_id: str, config: dict) -> None:
        config = {
            "unique_id": unique_id,
            "availability_topic": self._availability_topic(),
            "device": self._device_payload(),
            **config,
        }
        await self.client.publish(
            f"{self.discovery_prefix}/{component}/{unique_id}/config",
            json.dumps(config),
            retain=True,
        )

    def _backup_job_topic(self, job_name: str) -> str:
        return f"{self.topic_root}/backup/{slugify(job_name)}"

    def _backup_summary_topic(self) -> str:
        return f"{self.topic_root}/backup_summary"

    async def publish_backup_job_discovery(self, job_name: str) -> None:
        base = self._backup_job_topic(job_name)
        unique_prefix = f"{self.host_slug}_backup_{slugify(job_name)}"
        display_name = f"Backup: {job_name}"

        await self._publish_discovery("sensor", unique_prefix, {
            "name": display_name,
            "state_topic": f"{base}/state",
            "json_attributes_topic": f"{base}/attributes",
            "icon": "mdi:backup-restore",
        })
        await self._publish_discovery("sensor", f"{unique_prefix}_last_snapshot", {
            "name": f"{display_name} Last Snapshot",
            "state_topic": f"{base}/last_snapshot",
            "device_class": "timestamp",
        })
        await self._publish_discovery("binary_sensor", f"{unique_prefix}_problem", {
            "name": f"{display_name} Problem",
            "state_topic": f"{base}/problem",
            "device_class": "problem",
        })

    async def publish_backup_summary_discovery(self) -> None:
        base = self._backup_summary_topic()
        unique_prefix = f"{self.host_slug}_backup_summary"

        await self._publish_discovery("sensor", unique_prefix, {
            "name": "Backup: Overall",
            "state_topic": f"{base}/state",
            "json_attributes_topic": f"{base}/attributes",
            "icon": "mdi:backup-restore",
        })
        await self._publish_discovery("binary_sensor", f"{unique_prefix}_problem", {
            "name": "Backup: Problem",
            "state_topic": f"{base}/problem",
            "device_class": "problem",
        })
        await self._publish_discovery("sensor", f"{unique_prefix}_status_updated", {
            "name": "Backup: Status Updated",
            "state_topic": f"{base}/status_updated",
            "device_class": "timestamp",
        })

    async def publish_backup_job_state(
        self, job_name: str, state: str, problem: bool, last_snapshot: str | None, attributes: dict
    ) -> None:
        await self._publish_backup_states(
            self._backup_job_topic(job_name), state, problem, attributes,
            last_snapshot=last_snapshot,
        )

    async def publish_backup_summary_state(
        self, state: str, problem: bool, status_updated: str | None, attributes: dict
    ) -> None:
        await self._publish_backup_states(
            self._backup_summary_topic(), state, problem, attributes,
            status_updated=status_updated,
        )

    async def _publish_backup_states(
        self, base: str, state: str, problem: bool, attributes: dict, **timestamps
    ) -> None:
        await self.client.publish(f"{base}/state", state, retain=True)
        await self.client.publish(f"{base}/problem", "ON" if problem else "OFF", retain=True)
        await self.client.publish(f"{base}/attributes", json.dumps(attributes), retain=True)
        for key, value in timestamps.items():
            # "None" is what HA's MQTT sensor reads as "unknown".
            await self.client.publish(f"{base}/{key}", value or "None", retain=True)

    def _paperless_topic(self) -> str:
        return f"{self.topic_root}/paperless"

    async def publish_paperless_discovery(self) -> None:
        base = self._paperless_topic()
        await self._publish_discovery("sensor", f"{self.host_slug}_paperless_last_upload", {
            "name": "Paperless: Last Upload",
            "state_topic": f"{base}/last_upload",
            "json_attributes_topic": f"{base}/attributes",
            "device_class": "timestamp",
            "icon": "mdi:file-document-arrow-right",
        })

    async def publish_paperless_state(self, last_upload: str | None, attributes: dict) -> None:
        base = self._paperless_topic()
        # "None" is what HA's MQTT sensor reads as "unknown".
        await self.client.publish(f"{base}/last_upload", last_upload or "None", retain=True)
        await self.client.publish(f"{base}/attributes", json.dumps(attributes), retain=True)

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
