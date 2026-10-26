import json
from typing import List, Optional

import requests

from launcher_config import BASE_DIR, load_config, save_config

NOTIFICATIONS_URL = (
    "https://raw.githubusercontent.com/derpywe/ShinriTrialLauncher/main/notifications.json"
)
CACHE_PATH = BASE_DIR / "notifications_cache.json"
MAX_READ_IDS = 500


def _parse_item(item) -> Optional[dict]:
    if not isinstance(item, dict):
        return None
    nid = str(item.get("id", "")).strip()
    if not nid:
        return None
    return {
        "id": nid,
        "title": str(item.get("title", "")).strip() or "Уведомление",
        "body": str(item.get("body", "")).strip(),
        "date": str(item.get("date", "")).strip(),
    }


def _parse_items(data) -> List[dict]:
    if not isinstance(data, dict):
        return []
    items = data.get("notifications", [])
    if not isinstance(items, list):
        return []
    result = [_parse_item(i) for i in items]
    return [r for r in result if r]


def _load_cache() -> List[dict]:
    if not CACHE_PATH.exists():
        return []
    try:
        return _parse_items(json.loads(CACHE_PATH.read_text(encoding="utf-8")))
    except Exception:
        return []


def _save_cache(items: List[dict]) -> None:
    try:
        CACHE_PATH.write_text(
            json.dumps({"notifications": items}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def fetch_notifications(timeout: float = 5.0) -> List[dict]:
    """Тянет уведомления с GitHub; при ошибке возвращает последний кэш."""
    try:
        resp = requests.get(NOTIFICATIONS_URL, timeout=timeout)
        resp.raise_for_status()
        items = _parse_items(resp.json())
        _save_cache(items)
        return items
    except Exception:
        return _load_cache()


def get_read_ids() -> set:
    return set(load_config().get("read_notification_ids", []))


def has_unread(items: List[dict]) -> bool:
    if not items:
        return False
    read = get_read_ids()
    return any(n["id"] not in read for n in items)


def mark_all_read(items: List[dict]) -> None:
    if not items:
        return
    config = load_config()
    read = list(dict.fromkeys(config.get("read_notification_ids", [])))
    for n in items:
        if n["id"] not in read:
            read.append(n["id"])
    config["read_notification_ids"] = read[-MAX_READ_IDS:]
    save_config(config)