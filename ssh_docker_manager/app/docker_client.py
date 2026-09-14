import aiodocker


class DockerManager:
    def __init__(self, socket_path: str):
        self.docker = aiodocker.Docker(url=f"unix://{socket_path}")

    async def list_containers(self):
        return await self.docker.containers.list(all=True)

    async def start(self, container_id: str) -> None:
        await self.docker.containers.container(container_id).start()

    async def stop(self, container_id: str) -> None:
        await self.docker.containers.container(container_id).stop()

    async def restart(self, container_id: str) -> None:
        await self.docker.containers.container(container_id).restart()

    async def update_image(self, container_id: str) -> str:
        """Pulls the container's current image tag and recreates the container from it.
        Phase 1 limitation: recreation uses the container's existing Config/HostConfig
        as-is, so networks/volumes attached after creation via `docker network connect`
        etc. are not replayed. Fine for containers managed declaratively; revisit before
        exposing this to containers with ad-hoc runtime attachments."""
        container = self.docker.containers.container(container_id)
        info = await container.show()

        image = info["Config"]["Image"]
        name = info["Name"].lstrip("/")
        was_running = info["State"]["Running"]

        await self.docker.images.pull(image)

        await container.stop()
        await container.delete()

        new_config = dict(info["Config"])
        new_config["HostConfig"] = info["HostConfig"]
        new_container = await self.docker.containers.create(config=new_config, name=name)

        if was_running:
            await new_container.start()

        return new_container.id

    async def events(self):
        async for event in self.docker.events.subscribe():
            yield event

    async def close(self) -> None:
        await self.docker.close()
