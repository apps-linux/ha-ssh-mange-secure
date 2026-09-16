import shlex


class LibvirtError(Exception):
    pass


class LibvirtManager:
    """Talks to the remote libvirtd via `virsh` run over the existing SSH
    connection - no socket forwarding and no libvirt-python bindings, the same
    way DockerManager's Docker socket is the only thing that needs a tunnel."""

    def __init__(self, conn, connect_uri: str):
        self._conn = conn
        self._uri = connect_uri

    async def _virsh(self, *args: str, check: bool = True):
        command = "virsh -c " + shlex.quote(self._uri) + " " + " ".join(
            shlex.quote(a) for a in args
        )
        result = await self._conn.run(command, check=False)
        if check and result.exit_status != 0:
            raise LibvirtError(
                f"virsh {' '.join(args)} failed: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return result

    async def verify_access(self) -> None:
        result = await self._virsh("list", "--all", check=False)
        if result.exit_status != 0:
            raise PermissionError(
                "Configured account cannot talk to libvirt. Confirm virsh/libvirt-clients "
                "is installed on the remote host, libvirtd is running, and the account is "
                "a member of the 'libvirt' group and has re-logged in since being added: "
                f"{result.stderr.strip()}"
            )

    async def list_domains(self) -> list[dict]:
        result = await self._virsh("list", "--all")
        return parse_domain_list(result.stdout)

    async def start(self, name: str) -> None:
        await self._virsh("start", name)

    async def shutdown(self, name: str) -> None:
        await self._virsh("shutdown", name)

    async def reboot(self, name: str) -> None:
        await self._virsh("reboot", name)


def parse_domain_list(output: str) -> list[dict]:
    """Parses `virsh list --all` table output, e.g.:

         Id   Name                State
        ----------------------------------
         1    ubuntu-vm           running
         -    debian-vm           shut off

    Domain names containing spaces aren't supported: virsh's plain table
    output doesn't quote or delimit fields, so a name with a space can't be
    told apart from the start of a multi-word state (e.g. "shut off").
    """
    domains = []
    started = False

    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("---"):
            started = True
            continue
        if not started:
            continue

        parts = stripped.split(None, 2)
        if len(parts) < 3:
            continue

        _id, name, state = parts
        domains.append({"name": name, "state": state})

    return domains
