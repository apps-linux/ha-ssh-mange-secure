import json
from datetime import datetime, timedelta, timezone

from ssh_client import read_remote_file

# A status file whose "generated" time is older than this means the exporter
# on the remote host stopped running - every job value in it is then stale.
STATUS_FILE_MAX_AGE = timedelta(minutes=30)

# Job states, most severe first. "problem" is everything except ok/running.
JOB_OK = "ok"
JOB_RUNNING = "running"
JOB_FAILED = "failed"
JOB_STALE = "stale"
JOB_NO_SNAPSHOT = "no_snapshot"
JOB_TIMER_DISABLED = "timer_disabled"
JOB_ERROR = "error"
HEALTHY_JOB_STATES = (JOB_OK, JOB_RUNNING)

# Attributes copied from each job of the status file onto its HA sensor.
JOB_ATTRIBUTES = (
    "unit",
    "schedule",
    "timer_enabled",
    "next_run",
    "last_start",
    "last_end",
    "result",
    "exit_status",
    "latest_snapshot",
    "latest_snapshot_time",
    "snapshot_count",
    "folder",
    "error",
)


class BackupStatusReader:
    """Reads the JSON written by a backup status exporter on the remote host
    (e.g. a systemd timer running backup-status.py) over the existing SSH
    connection - a plain `cat`, no root and no extra port. The file format:

        {"generated": "<iso>", "backup_disk_mounted": true,
         "jobs": [{"name": "...", "result": "success", "exit_status": 0,
                   "running": false, "timer_enabled": true,
                   "latest_snapshot_time": "<iso>", ...}],
         "coverage": {"uncovered": [{"name": "..."}], ...}}
    """

    def __init__(self, conn, path: str):
        self._conn = conn
        self._path = path

    async def read(self) -> dict:
        return parse_status(await read_remote_file(self._conn, self._path))


def parse_status(text: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise RuntimeError(f"backup status file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise RuntimeError("backup status file has no 'jobs' list")
    data["jobs"] = [j for j in data["jobs"] if isinstance(j, dict) and j.get("name")]
    return data


def parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    # Naive timestamps can't be compared with an aware "now"; treat as UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def job_state(job: dict, now: datetime, max_age: timedelta) -> str:
    if job.get("error"):
        return JOB_ERROR
    if job.get("running"):
        return JOB_RUNNING
    exit_status = job.get("exit_status")
    if (job.get("result") not in (None, "success")) or (exit_status not in (None, 0)):
        return JOB_FAILED
    if job.get("timer_enabled") is False:
        return JOB_TIMER_DISABLED
    snapshot_time = parse_time(job.get("latest_snapshot_time"))
    if snapshot_time is None:
        return JOB_NO_SNAPSHOT
    if now - snapshot_time > max_age:
        return JOB_STALE
    return JOB_OK


def job_attributes(job: dict) -> dict:
    return {key: job.get(key) for key in JOB_ATTRIBUTES if key in job}


def summarize(status: dict, now: datetime, max_age: timedelta) -> dict:
    """Overall verdict for the whole status file: "ok" or "problem", plus
    the reasons as attributes."""
    problems = []
    job_states = {}
    for job in status["jobs"]:
        state = job_state(job, now, max_age)
        job_states[job["name"]] = state
        if state not in HEALTHY_JOB_STATES:
            problems.append(f"{job['name']}: {state}")

    generated = parse_time(status.get("generated"))
    if generated is None or now - generated > STATUS_FILE_MAX_AGE:
        problems.append("status file not updated recently")

    disk_mounted = status.get("backup_disk_mounted")
    if disk_mounted is False:
        problems.append("backup disk not mounted")

    coverage = status.get("coverage") or {}
    uncovered = [g.get("name") for g in coverage.get("uncovered") or [] if isinstance(g, dict)]
    if uncovered:
        problems.append("not in any backup: " + ", ".join(uncovered))

    return {
        "state": "problem" if problems else JOB_OK,
        "attributes": {
            "problems": problems,
            "generated": status.get("generated"),
            "backup_disk_mounted": disk_mounted,
            "jobs_total": len(job_states),
            "jobs_ok": sum(1 for s in job_states.values() if s in HEALTHY_JOB_STATES),
            "jobs": job_states,
            "uncovered": uncovered,
        },
    }
