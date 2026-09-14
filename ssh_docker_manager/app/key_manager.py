import os
import stat

import asyncssh

SSH_DIR = "/data/ssh"
PRIVATE_KEY_PATH = os.path.join(SSH_DIR, "id_ed25519")
PUBLIC_KEY_PATH = os.path.join(SSH_DIR, "id_ed25519.pub")
KNOWN_HOSTS_PATH = os.path.join(SSH_DIR, "known_hosts")
SHARE_DIR = "/share"


def ensure_key(
    key_mode: str,
    pasted_private_key: str | None,
    uploaded_file_name: str | None,
    passphrase: str | None,
) -> str:
    """Returns the path to a usable private key. Never returns key material itself.

    key_mode:
      - "generate": add-on creates a keypair on first run.
      - "paste": key text is supplied inline via ssh.private_key.
      - "upload": key file was dropped into the Home Assistant /share folder
        (via the Samba or File Editor add-on) and named in ssh.private_key_file.
    """
    os.makedirs(SSH_DIR, mode=0o700, exist_ok=True)

    if key_mode == "paste":
        if not pasted_private_key:
            raise ValueError("ssh.private_key is required when ssh.key_mode is 'paste'")
        _write_private_key(pasted_private_key)
        return PRIVATE_KEY_PATH

    if key_mode == "upload":
        if not uploaded_file_name:
            raise ValueError("ssh.private_key_file is required when ssh.key_mode is 'upload'")
        source_path = _resolve_share_path(uploaded_file_name)
        if not os.path.isfile(source_path):
            raise ValueError(
                f"No file named '{uploaded_file_name}' found in the Home Assistant /share "
                "folder. Upload the private key there (via the Samba add-on, the File Editor "
                "add-on, or SCP/SFTP) and match the filename in ssh.private_key_file."
            )
        with open(source_path) as f:
            _write_private_key(f.read())
        return PRIVATE_KEY_PATH

    if not os.path.exists(PRIVATE_KEY_PATH):
        key = asyncssh.generate_private_key("ssh-ed25519")
        key.write_private_key(PRIVATE_KEY_PATH)
        key.write_public_key(PUBLIC_KEY_PATH)
        os.chmod(PRIVATE_KEY_PATH, stat.S_IRUSR | stat.S_IWUSR)

    return PRIVATE_KEY_PATH


def _write_private_key(key_text: str) -> None:
    with open(PRIVATE_KEY_PATH, "w") as f:
        f.write(key_text.strip() + "\n")
    os.chmod(PRIVATE_KEY_PATH, stat.S_IRUSR | stat.S_IWUSR)


def _resolve_share_path(file_name: str) -> str:
    # Restrict to a plain filename directly under /share - no traversal via
    # subdirectories or "..", since this comes from add-on config text.
    safe_name = os.path.basename(file_name)
    if safe_name != file_name:
        raise ValueError("ssh.private_key_file must be a plain filename, not a path")
    return os.path.join(SHARE_DIR, safe_name)


def public_key_line() -> str | None:
    if os.path.exists(PUBLIC_KEY_PATH):
        with open(PUBLIC_KEY_PATH) as f:
            return f.read().strip()
    return None
