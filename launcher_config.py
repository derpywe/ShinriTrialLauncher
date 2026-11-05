import os
import json
from pathlib import Path
from typing import Any, Dict

APPDATA = os.getenv("APPDATA")
if APPDATA:
    BASE_DIR = Path(APPDATA) / "ShinriTrialLauncher"
else:
    BASE_DIR = Path.home() / ".shinri_trial_launcher"
BASE_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = BASE_DIR / "launcher_config.json"


def load_config() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_config(data: Dict[str, Any]) -> None:
    try:
        CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def add_recent_server(ip: str, port: int, name: str, limit: int = 10) -> None:
    config = load_config()
    recent = config.get("recent_servers", [])
    address = f"{ip}:{port}"
    recent = [s for s in recent if s.get("address") != address]
    recent.insert(0, {"ip": ip, "port": port, "name": name, "address": address})
    config["recent_servers"] = recent[:limit]
    save_config(config)


def get_recent_servers() -> list:
    return load_config().get("recent_servers", [])


def get_pinned_servers() -> set:
    """Множество адресов закреплённых серверов в формате 'ip:port'."""
    return set(load_config().get("pinned_servers", []))


def is_server_pinned(ip: str, port: int) -> bool:
    return f"{ip}:{port}" in get_pinned_servers()


def toggle_server_pin(ip: str, port: int) -> bool:
    """Переключает закрепление сервера. Возвращает новое состояние (True = закреплён)."""
    config = load_config()
    pinned = set(config.get("pinned_servers", []))
    address = f"{ip}:{port}"
    if address in pinned:
        pinned.discard(address)
        new_state = False
    else:
        pinned.add(address)
        new_state = True
    config["pinned_servers"] = sorted(pinned)
    save_config(config)
    return new_state