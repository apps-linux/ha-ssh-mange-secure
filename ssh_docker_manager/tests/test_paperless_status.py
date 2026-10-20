"""Offline test of the Paperless recent-uploads feature: stubs aiomqtt/asyncssh/aiodocker,
fakes SSH + MQTT. Run: python3 -I ssh_docker_manager/tests/test_paperless_status.py"""
import asyncio, json, os, sys, types
APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")
sys.path.insert(0, APP)
sys.modules["aiomqtt"] = types.SimpleNamespace(Client=object, MqttError=Exception)
for _m in ("asyncssh", "aiodocker"):
    sys.modules[_m] = types.SimpleNamespace(SSHClientConnection=object)
import paperless_status as ps
import mqtt_client, main

status = {"generated": "2026-10-20T15:00:00+02:00", "documents": [
    {"id": 519, "title": "Beitragsrechnung", "added": "2026-10-15T21:42:28+02:00", "owner": "daniel",
     "correspondent": "Itzehoer", "folder": "04 Versicherungen/Rechtsschutz/Itzehoer", "status": "uploaded",
     "business": False, "tags": ["ai-sorted", "nextcloud"], "nextcloud_file": None},
    {"id": 510, "title": "Kreditkontoauszug", "added": "2026-10-15T14:30:15+02:00", "folder": None,
     "status": "review"},
    "garbage"]}

data = ps.parse_uploads(json.dumps(status))
assert len(data["documents"]) == 2
state, attrs = ps.uploads_state(data)
assert state == "2026-10-15T21:42:28+02:00"
assert attrs["count"] == 2 and attrs["last_title"] == "Beitragsrechnung"
assert "tags" not in attrs["documents"][0] and attrs["documents"][0]["folder"].startswith("04 ")
assert ps.uploads_state({"documents": []}) == (None, {"generated": None, "count": 0, "last_title": None, "documents": []})
for txt in ("nope", '{"jobs": []}'):
    try: ps.parse_uploads(txt); raise SystemExit("should fail")
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
    await m.publish_paperless_discovery()
    cfg = json.loads(m.client.msgs["homeassistant/sensor/cahyos-h3_paperless_last_upload/config"])
    assert cfg["name"] == "Paperless: Last Upload" and cfg["device_class"] == "timestamp"
    conn = FakeConn(json.dumps(status))
    await main._publish_paperless_status(m, ps.PaperlessStatusReader(conn, "/var/lib/portal-paperless/recent.json"), "h")
    assert conn.cmd == "cat -- /var/lib/portal-paperless/recent.json"
    r = "ssh_manage_cahyos-h3/paperless"
    assert m.client.msgs[f"{r}/last_upload"] == "2026-10-15T21:42:28+02:00"
    assert json.loads(m.client.msgs[f"{r}/attributes"])["documents"][1]["status"] == "review"
    await main._publish_paperless_status(m, ps.PaperlessStatusReader(FakeConn("", 1), "/nope"), "h")
    assert m.client.msgs[f"{r}/last_upload"] == "None"
    assert "cannot read /nope" in json.loads(m.client.msgs[f"{r}/attributes"])["error"]
asyncio.run(go())
print("ALL OK")
