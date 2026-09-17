import asyncio
import re
import shlex

MEMINFO_LINE = re.compile(r"^(\w+):\s+(\d+)\s*kB")
CPU_LINE = re.compile(r"^cpu\s+(.*)")
CPU_FIELDS = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")


class HostMonitor:
    """Reads disk and memory usage from the remote host over the existing SSH
    connection - no agent, no extra port, the same low-footprint approach used
    for Docker (socket forward) and libvirt (virsh over SSH exec)."""

    def __init__(self, conn, disk_paths: list[str]):
        self._conn = conn
        self._disk_paths = disk_paths

    async def _run(self, command: str) -> str:
        result = await self._conn.run(command, check=False)
        if result.exit_status != 0:
            raise RuntimeError(f"'{command}' failed: {result.stderr.strip()}")
        return result.stdout

    async def get_memory(self) -> dict:
        output = await self._run("cat /proc/meminfo")
        return parse_meminfo(output)

    async def get_disk(self, path: str) -> dict:
        output = await self._run(f"df -P -B1 {shlex.quote(path)}")
        return parse_df(output, path)

    async def get_disks(self) -> dict[str, dict]:
        # Each path gets its own `df` call rather than one `df path1 path2 ...`
        # invocation: GNU df de-duplicates rows for paths sharing a filesystem
        # (e.g. "/" and "/home" on a single-partition install), which would
        # break a positional path-to-row mapping. Run them concurrently over
        # the same SSH connection instead - cheap, since a handful of exec
        # calls easily fits within one multiplexed session.
        results = await asyncio.gather(*(self.get_disk(path) for path in self._disk_paths))
        return dict(zip(self._disk_paths, results))

    async def get_cpu_stat(self) -> dict:
        output = await self._run("cat /proc/stat")
        return parse_cpu_stat(output)


def parse_meminfo(output: str) -> dict:
    values = {}
    for line in output.splitlines():
        match = MEMINFO_LINE.match(line)
        if match:
            values[match.group(1)] = int(match.group(2)) * 1024  # kB -> bytes

    total = values.get("MemTotal", 0)
    if "MemAvailable" in values:
        available = values["MemAvailable"]
    else:
        # Kernels older than 3.14 don't report MemAvailable.
        available = (
            values.get("MemFree", 0) + values.get("Buffers", 0) + values.get("Cached", 0)
        )

    used = max(total - available, 0)
    return {"total": total, "used": used, "available": available}


def parse_df(output: str, path: str) -> dict:
    lines = [line for line in output.splitlines() if line.strip()]
    if len(lines) < 2:
        raise RuntimeError(f"Unexpected 'df' output for {path}: {output!r}")

    # Filesystem 1B-blocks Used Available Capacity Mounted-on
    fields = lines[1].split()
    if len(fields) < 4:
        raise RuntimeError(f"Unexpected 'df' output for {path}: {output!r}")

    total, used, available = int(fields[1]), int(fields[2]), int(fields[3])
    return {"total": total, "used": used, "available": available}


def parse_cpu_stat(output: str) -> dict:
    """Parses the aggregate "cpu " line of /proc/stat, e.g.:

        cpu  74608 2520 24433 1160535 8434 0 1284 0 0 0

    into named jiffy counters. Per-core "cpu0", "cpu1", ... lines are
    ignored - only the aggregate matters for a single overall usage figure.
    """
    for line in output.splitlines():
        match = CPU_LINE.match(line)
        if match:
            values = [int(p) for p in match.group(1).split()]
            # iowait/irq/softirq/steal were added to /proc/stat in later
            # kernels than user/nice/system/idle - pad with 0 if missing.
            values += [0] * (len(CPU_FIELDS) - len(values))
            return dict(zip(CPU_FIELDS, values[: len(CPU_FIELDS)]))
    raise RuntimeError(f"No 'cpu ' line found in /proc/stat output: {output!r}")


def cpu_percent(prev: dict, curr: dict) -> float | None:
    """CPU utilization (%) between two /proc/stat samples, the same delta
    technique `top`/`htop` use. Returns None if the samples don't support a
    valid delta (e.g. a reboot reset the counters between polls) rather than
    publishing a nonsensical value."""
    prev_idle = prev["idle"] + prev["iowait"]
    curr_idle = curr["idle"] + curr["iowait"]
    total_delta = sum(curr.values()) - sum(prev.values())
    idle_delta = curr_idle - prev_idle

    if total_delta <= 0:
        return None

    usage = 100 * (total_delta - idle_delta) / total_delta
    return round(max(0.0, min(100.0, usage)), 1)


def bytes_to_gb(value: int) -> float:
    return round(value / 1_000_000_000, 2)


def percent(used: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((used / total) * 100, 1)
