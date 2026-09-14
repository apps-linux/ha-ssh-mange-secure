import os
import stat

import asyncssh

SSH_DIR = "/data/ssh"
PRIVATE_KEY_PATH = os.path.join(SSH_DIR, "id_ed25519")
PUBLIC_KEY_PATH = os.path.join(SSH_DIR, "id_ed25519.pub")
KNOWN_HOSTS_PATH = os.path.join(SSH_DIR, "known_hosts")


def ensure_key(key_mode: str, existing_private_key: str | None, passphrase: str | None) -> str:
    """Returns the path to a usable private key, either generating one or
    persisting the user-supplied key. Never returns key material itself."""
    os.makedirs(SSH_DIR, mode=0o700, exist_ok=True)

    if key_mode == "existing":
        if not existing_private_key:
            raise ValueError("ssh.private_key is required when ssh.key_mode is 'existing'")
        with open(PRIVATE_KEY_PATH, "w") as f:
            f.write(existing_private_key.strip() + "\n")
        os.chmod(PRIVATE_KEY_PATH, stat.S_IRUSR | stat.S_IWUSR)
        return PRIVATE_KEY_PATH

    if not os.path.exists(PRIVATE_KEY_PATH):
        key = asyncssh.generate_private_key("ssh-ed25519")
        key.write_private_key(PRIVATE_KEY_PATH)
        key.write_public_key(PUBLIC_KEY_PATH)
        os.chmod(PRIVATE_KEY_PATH, stat.S_IRUSR | stat.S_IWUSR)

    return PRIVATE_KEY_PATH


def public_key_line() -> str | None:
    if os.path.exists(PUBLIC_KEY_PATH):
        with open(PUBLIC_KEY_PATH) as f:
            return f.read().strip()
    return None
