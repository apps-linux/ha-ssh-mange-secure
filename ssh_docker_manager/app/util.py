import re


def slugify(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_") or "unnamed"


def slugify_path(path: str) -> str:
    # slugify("/") collapses to nothing since every character is stripped;
    # special-case the (very common) root path to a readable "root" instead
    # of falling through to slugify's generic "unnamed".
    if path.strip("/") == "":
        return "root"
    return slugify(path)


def _strip_wrapping_quotes(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def parse_disk_paths(raw: str | list | None) -> list[str]:
    """monitoring_disk_paths is a comma-separated string per server entry.
    A list nested inside a servers[] entry fails Supervisor's schema
    validation ("Invalid list for option 'monitoring_disk_paths'") even
    though the servers list itself is valid, so this can't be a nested
    list - see README. Also accepts an actual list, for robustness (e.g. a
    hand-edited options.json).

    The field is a plain string, so if someone types "/, /opt/nextcloud"
    including the quote marks (a natural thing to do for a comma-separated
    value), those quote characters become part of the literal string rather
    than being stripped - producing paths like '"/' that don't exist.
    Tolerate one layer of wrapping quotes on the whole value and on each
    individual entry."""
    if isinstance(raw, list):
        paths = raw
    else:
        paths = _strip_wrapping_quotes((raw or "").strip()).split(",")

    cleaned = [_strip_wrapping_quotes(p.strip()) for p in paths if p.strip()]
    cleaned = [p for p in cleaned if p]
    return cleaned or ["/"]
