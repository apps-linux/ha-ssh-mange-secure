import json
import os

OPTIONS_PATH = "/data/options.json"


def load_options() -> dict:
    with open(OPTIONS_PATH) as f:
        return json.load(f)


def resolve_mqtt(options: dict) -> dict:
    """Prefer the Supervisor-injected Mosquitto add-on credentials (services: mqtt:want)
    unless the user opted out or supplied their own broker."""
    mqtt_opts = options.get("mqtt", {})

    if mqtt_opts.get("use_addon_broker", True) and os.environ.get("MQTT_HOST"):
        return {
            "host": os.environ["MQTT_HOST"],
            "port": int(os.environ.get("MQTT_PORT", 1883)),
            "username": os.environ.get("MQTT_USERNAME", ""),
            "password": os.environ.get("MQTT_PASSWORD", ""),
        }

    return {
        "host": mqtt_opts.get("host", ""),
        "port": mqtt_opts.get("port", 1883),
        "username": mqtt_opts.get("username", ""),
        "password": mqtt_opts.get("password", ""),
    }
