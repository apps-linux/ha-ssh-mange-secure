"""Offline test of name-based Docker discovery + stale-config pruning: stubs aiomqtt/asyncssh/aiodocker,
fakes MQTT + Docker. Run: python3 -I ssh_docker_manager/tests/test_docker_discovery.py"""
import asyncio, json, os, sys, types
APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")
sys.path.insert(0, APP)
sys.modules["aiomqtt"] = types.SimpleNamespace(Client=object, MqttError=Exception)
for _m in ("asyncssh", "aiodocker"):
    sys.modules[_m] = types.SimpleNamespace(SSHClientConnection=object)
import mqtt_client, main

P = "homeassistant"
retained = {
    f"{P}/switch/cahyos-h3_docker_7f94eaa36f01/config",          # old ID-based scheme
    f"{P}/button/cahyos-h3_docker_7f94eaa36f01_restart/config",
    f"{P}/update/cahyos-h3_docker_7f94eaa36f01_update/config",
    f"{P}/switch/cahyos-h3_docker_traefik/config",                # current container, keep
    f"{P}/switch/cahyos-h3_docker_euro-office-docs/config",       # removed container
    f"{P}/switch/cahyos-h3_vm_homeassistant/config",              # VM, not ours to prune
    f"{P}/sensor/cahyos-h3_host_cpu_use_percent/config",          # host sensor
    f"{P}/switch/otherhost_docker_traefik/config",                # other server
}

class Msg:
    def __init__(self, topic, payload=b"{}", retain=True):
        self.topic, self.payload, self.retain = topic, payload, retain

class FakeMessages:
    def __init__(self, msgs): self.msgs = list(msgs)
    def __aiter__(self): return self
    async def __anext__(self):
        if self.msgs: return self.msgs.pop(0)
        await asyncio.sleep(3600)

class FakeClient:
    def __init__(self, msgs=()):
        self.msgs, self.subs, self.unsubs = {}, [], []
        self.messages = FakeMessages(msgs)
    async def publish(self, topic, payload, retain=False): self.msgs[topic] = (payload, retain)
    async def subscribe(self, topic): self.subs.append(topic)
    async def unsubscribe(self, topic): self.unsubs.append(topic)

def bridge(client):
    m = mqtt_client.MqttBridge("h", 1883, "", "", P, "cahyos-h3")
    m.client = client
    return m

async def go():
    # Discovery is keyed on the container name, topics too.
    c = FakeClient()
    m = bridge(c)
    await m.publish_docker_discovery("traefik")
    sw = json.loads(c.msgs[f"{P}/switch/cahyos-h3_docker_traefik/config"][0])
    assert sw["unique_id"] == "cahyos-h3_docker_traefik_power"
    assert sw["state_topic"] == "ssh_manage_cahyos-h3/docker/traefik/state"
    assert sw["command_topic"] == "ssh_manage_cahyos-h3/docker/traefik/set"
    btn = json.loads(c.msgs[f"{P}/button/cahyos-h3_docker_traefik_restart/config"][0])
    assert btn["unique_id"] == "cahyos-h3_docker_traefik_restart"
    assert set(m.docker_discovery_topics("traefik")) == set(c.msgs)

    # Pruning clears only this host's Docker configs that aren't current.
    msgs = [Msg(t) for t in sorted(retained)] + [Msg(f"{P}/switch/cahyos-h3_docker_gone/config", b"")]
    c = FakeClient(msgs)
    m = bridge(c)
    stale = await m.prune_docker_discovery(["traefik"])
    assert stale == sorted([
        f"{P}/button/cahyos-h3_docker_7f94eaa36f01_restart/config",
        f"{P}/switch/cahyos-h3_docker_7f94eaa36f01/config",
        f"{P}/switch/cahyos-h3_docker_euro-office-docs/config",
        f"{P}/update/cahyos-h3_docker_7f94eaa36f01_update/config",
    ]), stale
    assert all(c.msgs[t] == (b"", True) for t in stale) and set(c.msgs) == set(stale)
    assert c.subs == c.unsubs and len(c.subs) == 3

    # Docker events: real status by name, new containers get discovered, exec noise ignored.
    class FakeDocker:
        def __init__(self, events, status): self._events, self._status = events, status
        async def events(self):
            for e in self._events: yield e
        async def status(self, name): return self._status.get(name)
    ev = lambda action, name: {"Type": "container", "Action": action, "Actor": {"Attributes": {"name": name}}}
    events = [ev("die", "traefik"), ev("start", "newbie"), ev("exec_start: sh", "traefik"),
              ev("start", "gone"), {"Type": "network", "Action": "connect"}]
    c = FakeClient()
    m = bridge(c)
    known = {"traefik"}
    await main._handle_docker_events(m, FakeDocker(events, {"traefik": "exited", "newbie": "running"}), known)
    assert c.msgs["ssh_manage_cahyos-h3/docker/traefik/state"][0] == "exited"
    assert c.msgs["ssh_manage_cahyos-h3/docker/newbie/state"][0] == "running"
    assert f"{P}/switch/cahyos-h3_docker_newbie/config" in c.msgs
    assert f"{P}/switch/cahyos-h3_docker_traefik/config" not in c.msgs
    assert not any("gone" in t for t in c.msgs) and known == {"traefik", "newbie"}

asyncio.run(go())
print("OK")
