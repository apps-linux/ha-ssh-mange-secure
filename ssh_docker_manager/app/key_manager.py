import os
import stat

import asyncssh

from util import slugify

SSH_DIR = "/data/ssh"
KNOWN_HOSTS_PATH = os.path.join(SSH_DIR, "known_hosts")


def _server_dir(server_slug: str) -> str:
    return os.path.join(SSH_DIR, server_slug)


def _private_key_path(server_slug: str) -> str:
    return os.path.join(_server_dir(server_slug), "id_ed25519")


def _public_key_path(server_slug: str) -> str:
    return os.path.join(_server_dir(server_slug), "id_ed25519.pub")


def ensure_key(
    server_slug: str, key_mode: str, pasted_private_key: str | None, passphrase: str | None
) -> str:
    """Returns the path to a usable private key for one server. Never returns
    key material itself. Each server gets its own key under /data/ssh/<slug>,
    where <slug> is derived from that server's configured host - keys aren't
    shared between servers.

    key_mode:
      - "generate": add-on creates a keypair on first run.
      - "paste": key text is supplied inline via that server's private_key.
    """
    os.makedirs(SSH_DIR, mode=0o700, exist_ok=True)
    os.makedirs(_server_dir(server_slug), mode=0o700, exist_ok=True)

    private_key_path = _private_key_path(server_slug)

    if key_mode == "paste":
        if not pasted_private_key:
            raise ValueError(f"private_key is required for '{server_slug}' when key_mode is 'paste'")
        _validate_private_key(pasted_private_key, passphrase)
        _write_private_key(private_key_path, pasted_private_key)
        return private_key_path

    if not os.path.exists(private_key_path):
        key = asyncssh.generate_private_key("ssh-ed25519")
        key.write_private_key(private_key_path)
        key.write_public_key(_public_key_path(server_slug))
        os.chmod(private_key_path, stat.S_IRUSR | stat.S_IWUSR)

    return private_key_path


def _validate_private_key(key_text: str, passphrase: str | None) -> None:
    try:
        asyncssh.import_private_key(key_text, passphrase=passphrase or None)
    except asyncssh.KeyImportError as exc:
        raise ValueError(
            f"private_key does not parse as a valid private key ({exc}). This "
            "usually means the multi-line key text lost its line breaks. In the "
            "add-on's Configuration tab, switch to 'Edit in YAML' and use a literal "
            "block scalar so newlines are preserved, e.g.:\n"
            "  servers:\n"
            "    - private_key: |\n"
            "        -----BEGIN OPENSSH PRIVATE KEY-----\n"
            "        ...\n"
            "        -----END OPENSSH PRIVATE KEY-----\n"
            "A plain multi-line value without the '|' gets its line breaks folded "
            "into spaces by YAML, which corrupts the key."
        ) from exc


def _write_private_key(path: str, key_text: str) -> None:
    with open(path, "w") as f:
        f.write(key_text.strip() + "\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def public_key_line(server_slug: str) -> str | None:
    path = _public_key_path(server_slug)
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    return None


def server_slug(host: str) -> str:
    return slugify(host)
