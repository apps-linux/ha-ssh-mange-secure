import json
import os

OPTIONS_PATH = "/data/options.json"


def load_options() -> dict:
    with open(OPTIONS_PATH) as f:
        return json.load(f)


DEFAULT_HA_MQTT_HOST = "core-mosquitto"
DEFAULT_HA_MQTT_PORT = 1883


def resolve_mqtt(options: dict) -> dict:
    """mqtt.broker_mode picks the source explicitly:
    - "homeassistant": prefer the Supervisor-injected Mosquitto add-on credentials
      (from `services: ["mqtt:want"]`); if those aren't present - e.g. service
      discovery hasn't propagated yet - fall back to core-mosquitto:1883, the
      fixed internal address every Home Assistant install with the Mosquitto
      add-on installed can reach.
    - "external": use the host/port/username/password fields below, for a
      separate/standalone MQTT broker.
    """
    mqtt_opts = options.get("mqtt", {})
    mode = mqtt_opts.get("broker_mode", "homeassistant")

    if mode == "homeassistant":
        if os.environ.get("MQTT_HOST"):
            return {
                "host": os.environ["MQTT_HOST"],
                "port": int(os.environ.get("MQTT_PORT", DEFAULT_HA_MQTT_PORT)),
                "username": os.environ.get("MQTT_USERNAME", ""),
                "password": os.environ.get("MQTT_PASSWORD", ""),
            }
        return {
            "host": mqtt_opts.get("host") or DEFAULT_HA_MQTT_HOST,
            "port": mqtt_opts.get("port") or DEFAULT_HA_MQTT_PORT,
            "username": mqtt_opts.get("username", ""),
            "password": mqtt_opts.get("password", ""),
        }

    if not mqtt_opts.get("host"):
        raise RuntimeError(
            "mqtt.broker_mode is 'external' but mqtt.host is not set."
        )
    return {
        "host": mqtt_opts["host"],
        "port": mqtt_opts.get("port", 1883),
        "username": mqtt_opts.get("username", ""),
        "password": mqtt_opts.get("password", ""),
    }
