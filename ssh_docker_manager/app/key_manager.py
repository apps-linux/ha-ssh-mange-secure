import os
import stat

import asyncssh

SSH_DIR = "/data/ssh"
PRIVATE_KEY_PATH = os.path.join(SSH_DIR, "id_ed25519")
PUBLIC_KEY_PATH = os.path.join(SSH_DIR, "id_ed25519.pub")
KNOWN_HOSTS_PATH = os.path.join(SSH_DIR, "known_hosts")


def ensure_key(key_mode: str, pasted_private_key: str | None, passphrase: str | None) -> str:
    """Returns the path to a usable private key. Never returns key material itself.

    key_mode:
      - "generate": add-on creates a keypair on first run.
      - "paste": key text is supplied inline via ssh.private_key.
    """
    os.makedirs(SSH_DIR, mode=0o700, exist_ok=True)

    if key_mode == "paste":
        if not pasted_private_key:
            raise ValueError("ssh.private_key is required when ssh.key_mode is 'paste'")
        _validate_private_key(pasted_private_key, passphrase)
        _write_private_key(pasted_private_key)
        return PRIVATE_KEY_PATH

    if not os.path.exists(PRIVATE_KEY_PATH):
        key = asyncssh.generate_private_key("ssh-ed25519")
        key.write_private_key(PRIVATE_KEY_PATH)
        key.write_public_key(PUBLIC_KEY_PATH)
        os.chmod(PRIVATE_KEY_PATH, stat.S_IRUSR | stat.S_IWUSR)

    return PRIVATE_KEY_PATH


def _validate_private_key(key_text: str, passphrase: str | None) -> None:
    try:
        asyncssh.import_private_key(key_text, passphrase=passphrase or None)
    except asyncssh.KeyImportError as exc:
        raise ValueError(
            f"ssh.private_key does not parse as a valid private key ({exc}). This "
            "usually means the multi-line key text lost its line breaks. In the "
            "add-on's Configuration tab, switch to 'Edit in YAML' and use a literal "
            "block scalar so newlines are preserved, e.g.:\n"
            "  ssh:\n"
            "    private_key: |\n"
            "      -----BEGIN OPENSSH PRIVATE KEY-----\n"
            "      ...\n"
            "      -----END OPENSSH PRIVATE KEY-----\n"
            "A plain multi-line value without the '|' gets its line breaks folded "
            "into spaces by YAML, which corrupts the key."
        ) from exc


def _write_private_key(key_text: str) -> None:
    with open(PRIVATE_KEY_PATH, "w") as f:
        f.write(key_text.strip() + "\n")
    os.chmod(PRIVATE_KEY_PATH, stat.S_IRUSR | stat.S_IWUSR)


def public_key_line() -> str | None:
    if os.path.exists(PUBLIC_KEY_PATH):
        with open(PUBLIC_KEY_PATH) as f:
            return f.read().strip()
    return None
