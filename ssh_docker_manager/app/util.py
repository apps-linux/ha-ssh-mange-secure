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


def parse_disk_paths(raw: str | list | None) -> list[str]:
    """monitoring_disk_paths is a comma-separated string per server entry.
    A list nested inside a servers[] entry fails Supervisor's schema
    validation ("Invalid list for option 'monitoring_disk_paths'") even
    though the servers list itself is valid, so this can't be a nested
    list - see README. Also accepts an actual list, for robustness (e.g. a
    hand-edited options.json)."""
    if isinstance(raw, list):
        paths = raw
    else:
        paths = (raw or "").split(",")

    cleaned = [p.strip() for p in paths if p.strip()]
    return cleaned or ["/"]
