import logging
import os

import asyncssh

from key_manager import KNOWN_HOSTS_PATH

log = logging.getLogger(__name__)

REMOTE_DOCKER_SOCK = "/var/run/docker.sock"
LOCAL_DOCKER_SOCK = "/tmp/docker_forward.sock"


async def connect(
    host: str, port: int, username: str, client_keys: list, passphrase: str | None
) -> asyncssh.SSHClientConnection:
    # known_hosts=None on first run means "accept whatever key the server presents".
    # That first connection is trust-on-first-use, not verified — if this matters for
    # your threat model, confirm the host's fingerprint out-of-band before enabling
    # the add-on. Every connection after that is checked against the pinned file below.
    known_hosts = KNOWN_HOSTS_PATH if os.path.exists(KNOWN_HOSTS_PATH) else None

    conn = await asyncssh.connect(
        host,
        port=port,
        username=username,
        client_keys=client_keys,
        passphrase=passphrase or None,
        known_hosts=known_hosts,
    )

    if known_hosts is None:
        await _pin_host_key(conn, host, port)

    return conn


async def _pin_host_key(conn: asyncssh.SSHClientConnection, host: str, port: int) -> None:
    remote_key = conn.get_extra_info("server_host_key")
    entry = f"[{host}]:{port} {remote_key.export_public_key().decode().strip()}\n"
    with open(KNOWN_HOSTS_PATH, "a") as f:
        f.write(entry)
    log.warning("Pinned new host key for %s:%s on first connect", host, port)


async def verify_docker_access(conn: asyncssh.SSHClientConnection) -> str:
    result = await conn.run("docker version --format '{{.Server.Version}}'", check=False)
    if result.exit_status != 0:
        raise PermissionError(
            "Configured account cannot reach the Docker socket. Confirm it is a "
            "member of the 'docker' group on the remote host and has re-logged in "
            f"since being added: {result.stderr.strip()}"
        )
    return result.stdout.strip()


async def forward_docker_socket(conn: asyncssh.SSHClientConnection):
    if os.path.exists(LOCAL_DOCKER_SOCK):
        os.remove(LOCAL_DOCKER_SOCK)

    listener = await conn.forward_local_path(LOCAL_DOCKER_SOCK, REMOTE_DOCKER_SOCK)
    return listener, LOCAL_DOCKER_SOCK
