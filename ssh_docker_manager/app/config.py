import json
import os

OPTIONS_PATH = "/data/options.json"


def load_options() -> dict:
    with open(OPTIONS_PATH) as f:
        return json.load(f)


def resolve_mqtt(options: dict) -> dict:
    """mqtt.broker_mode picks the source explicitly:
    - "homeassistant": use the Supervisor-injected Mosquitto add-on credentials
      (requires `services: ["mqtt:want"]` in config.yaml and the add-on installed).
    - "external": use the host/port/username/password fields below, for a
      separate/standalone MQTT broker.
    """
    mqtt_opts = options.get("mqtt", {})
    mode = mqtt_opts.get("broker_mode", "homeassistant")

    if mode == "homeassistant":
        if not os.environ.get("MQTT_HOST"):
            raise RuntimeError(
                "mqtt.broker_mode is 'homeassistant' but no Mosquitto add-on broker "
                "was found. Install the Mosquitto broker add-on, or switch "
                "mqtt.broker_mode to 'external' and fill in mqtt.host/port/username/password."
            )
        return {
            "host": os.environ["MQTT_HOST"],
            "port": int(os.environ.get("MQTT_PORT", 1883)),
            "username": os.environ.get("MQTT_USERNAME", ""),
            "password": os.environ.get("MQTT_PASSWORD", ""),
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
