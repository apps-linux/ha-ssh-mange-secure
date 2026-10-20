"""Offline test of the backup status feature: stubs aiomqtt/asyncssh/aiodocker,
fakes SSH + MQTT. Run: python3 -I ssh_docker_manager/tests/test_backup_status.py"""
import asyncio, json, sys, types
from datetime import datetime, timedelta, timezone
import os
APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")
sys.path.insert(0, APP)
# stub aiomqtt (not installed locally)
sys.modules["aiomqtt"] = types.SimpleNamespace(Client=object, MqttError=Exception)
for _m in ("asyncssh", "aiodocker"): sys.modules[_m] = types.SimpleNamespace(SSHClientConnection=object)
import backup_status as bs
import mqtt_client, main

now = datetime.now(timezone.utc)
iso = lambda d: d.astimezone().isoformat(timespec="seconds")
status = {
  "generated": iso(now - timedelta(minutes=2)), "backup_disk_mounted": True,
  "jobs": [
    {"name": "nextcloud", "unit": "backup-nextcloud.service", "schedule": "03:00", "timer_enabled": True,
     "running": False, "result": "success", "exit_status": 0, "latest_snapshot": "x",
     "latest_snapshot_time": iso(now - timedelta(hours=10)), "snapshot_count": 14},
    {"name": "lima", "timer_enabled": True, "running": False, "result": "exit-code", "exit_status": 1,
     "latest_snapshot_time": iso(now - timedelta(hours=30))},
    {"name": "wiki", "timer_enabled": True, "running": False, "result": "success", "exit_status": 0,
     "latest_snapshot_time": iso(now - timedelta(hours=30))},
    {"name": "infra", "timer_enabled": True, "running": True, "result": "success", "exit_status": 0,
     "latest_snapshot_time": None},
    {"name": "new", "timer_enabled": True, "running": False, "result": "success", "exit_status": 0,
     "latest_snapshot_time": None},
    {"name": "off", "timer_enabled": False, "result": "success", "exit_status": 0,
     "latest_snapshot_time": iso(now)},
    {"name": "broken", "error": "TimeoutExpired: x"},
  ],
  "coverage": {"covered": [], "ignored": [], "uncovered": [{"name": "gladys"}], "error": None},
}
age = timedelta(hours=26)
got = {j["name"]: bs.job_state(j, now, age) for j in status["jobs"]}
assert got == {"nextcloud": "ok", "lima": "failed", "wiki": "stale", "infra": "running",
               "new": "no_snapshot", "off": "timer_disabled", "broken": "error"}, got
s = bs.summarize(status, now, age)
assert s["state"] == "problem" and s["attributes"]["jobs_ok"] == 2, s
assert "not in any backup: gladys" in s["attributes"]["problems"]
# all-good case
good = {"generated": iso(now), "backup_disk_mounted": True, "jobs": [status["jobs"][0]], "coverage": {"uncovered": []}}
assert bs.summarize(good, now, age)["state"] == "ok"
# stale exporter / unmounted disk
bad = dict(good, generated=iso(now - timedelta(hours=1)), backup_disk_mounted=False)
assert bs.summarize(bad, now, age)["attributes"]["problems"] == ["status file not updated recently", "backup disk not mounted"]
for txt in ("not json", '{"x": 1}'):
    try: bs.parse_status(txt); raise SystemExit("should fail")
    except RuntimeError: pass

class FakeClient:
    def __init__(self): self.msgs = {}
    async def publish(self, topic, payload, retain=False): self.msgs[topic] = payload
class FakeConn:
    def __init__(self, out, rc=0): self.out, self.rc = out, rc
    async def run(self, cmd, check=False):
        self.cmd = cmd
        return types.SimpleNamespace(exit_status=self.rc, stdout=self.out, stderr="No such file")

async def go():
    m = mqtt_client.MqttBridge("h", 1883, "", "", "homeassistant", "cahyos-h3")
    m.client = FakeClient()
    known = set()
    conn = FakeConn(json.dumps(status))
    await m.publish_backup_summary_discovery()
    await main._publish_backup_status(m, bs.BackupStatusReader(conn, "/var/lib/portal-backup/status.json"), age, known, "cahyos-h3")
    assert conn.cmd == "cat -- /var/lib/portal-backup/status.json"
    msgs = m.client.msgs
    r = "ssh_manage_cahyos-h3"
    assert msgs[f"{r}/backup/nextcloud/state"] == "ok"
    assert msgs[f"{r}/backup/nextcloud/problem"] == "OFF"
    assert msgs[f"{r}/backup/lima/problem"] == "ON"
    assert msgs[f"{r}/backup/new/last_snapshot"] == "None"
    assert msgs[f"{r}/backup_summary/state"] == "problem"
    cfg = json.loads(msgs["homeassistant/sensor/cahyos-h3_backup_nextcloud/config"])
    assert cfg["name"] == "Backup: nextcloud" and cfg["json_attributes_topic"] == f"{r}/backup/nextcloud/attributes"
    assert cfg["device"]["identifiers"] == [r]
    cfg = json.loads(msgs["homeassistant/binary_sensor/cahyos-h3_backup_summary_problem/config"])
    assert cfg["device_class"] == "problem"
    print(json.dumps(json.loads(msgs[f"{r}/backup_summary/attributes"]), indent=1))
    # unreadable file -> error state, no exception
    await main._publish_backup_status(m, bs.BackupStatusReader(FakeConn("", 1), "/nope"), age, known, "h")
    assert msgs[f"{r}/backup_summary/state"] == "error" and msgs[f"{r}/backup_summary/problem"] == "ON"
asyncio.run(go())
print("ALL OK")
