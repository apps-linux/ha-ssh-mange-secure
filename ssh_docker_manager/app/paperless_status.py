import json

from ssh_client import read_remote_file

# Fields of each document passed on to the HA sensor's "documents" attribute.
DOCUMENT_FIELDS = ("id", "title", "added", "owner", "correspondent", "folder", "status", "business")


class PaperlessStatusReader:
    """Reads the JSON written by a recent-uploads exporter on the remote host
    (e.g. a systemd timer querying Paperless-ngx) over the existing SSH
    connection. The file format:

        {"generated": "<iso>",
         "documents": [{"id": 519, "title": "...", "added": "<iso>",
                        "owner": "...", "folder": "...", "status": "uploaded", ...}]}

    newest document first.
    """

    def __init__(self, conn, path: str):
        self._conn = conn
        self._path = path

    async def read(self) -> dict:
        return parse_uploads(await read_remote_file(self._conn, self._path))


def parse_uploads(text: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise RuntimeError(f"paperless status file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("documents"), list):
        raise RuntimeError("paperless status file has no 'documents' list")
    data["documents"] = [d for d in data["documents"] if isinstance(d, dict)]
    return data


def uploads_state(data: dict) -> tuple[str | None, dict]:
    """(state, attributes) for the "Paperless: Last Upload" sensor: the newest
    document's added time, and the documents (trimmed to DOCUMENT_FIELDS)."""
    docs = [{k: d.get(k) for k in DOCUMENT_FIELDS if k in d} for d in data["documents"]]
    latest = docs[0].get("added") if docs else None
    return latest, {
        "generated": data.get("generated"),
        "count": len(docs),
        "last_title": docs[0].get("title") if docs else None,
        "documents": docs,
    }
