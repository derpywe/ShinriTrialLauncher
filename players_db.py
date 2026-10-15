import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from launcher_config import BASE_DIR

PLAYERS_PATH = BASE_DIR / "players.json"
MAX_SESSIONS_PER_PLAYER = 60


def _load_raw() -> Dict[str, dict]:
    if not PLAYERS_PATH.exists():
        return {}
    try:
        return json.loads(PLAYERS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_raw(data: Dict[str, dict]) -> None:
    try:
        PLAYERS_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def record_encounter(players, server: str, map_name: str, when: Optional[datetime] = None) -> int:
    when = when or datetime.now()
    stamp = when.isoformat(timespec="seconds")
    data = _load_raw()
    new_count = 0

    for player in players:
        key = player.steam_id64
        if not key:
            continue

        entry = data.get(key)
        if entry is None:
            new_count += 1
            entry = {
                "steam_id64": key,
                "steam_id": player.steam_id,
                "names": [],
                "first_seen": stamp,
                "last_seen": stamp,
                "encounters": 0,
                "sessions": [],
                "comment": "",
            }
            data[key] = entry

        if player.name not in entry["names"]:
            entry["names"].insert(0, player.name)
            del entry["names"][8:]

        entry["last_seen"] = stamp

        sessions = entry.setdefault("sessions", [])
        same_session = (
            sessions
            and sessions[0].get("server") == server
            and sessions[0].get("date", "")[:10] == stamp[:10]
        )
        if same_session:
            sessions[0]["date"] = stamp
        else:
            sessions.insert(0, {"date": stamp, "server": server, "map": map_name, "name": player.name})
            del sessions[MAX_SESSIONS_PER_PLAYER:]
            entry["encounters"] = entry.get("encounters", 0) + 1

    _save_raw(data)
    return new_count


def get_all_players() -> List[dict]:
    data = _load_raw()
    players = list(data.values())
    players.sort(key=lambda p: p.get("last_seen", ""), reverse=True)
    return players


def get_player(steam_id64: str) -> Optional[dict]:
    return _load_raw().get(steam_id64)


def set_comment(steam_id64: str, comment: str) -> None:
    data = _load_raw()
    entry = data.get(steam_id64)
    if entry is None:
        return
    entry["comment"] = comment
    _save_raw(data)


def get_comments_map() -> Dict[str, str]:
    return {
        key: entry.get("comment", "")
        for key, entry in _load_raw().items()
        if entry.get("comment")
    }


def clear_all() -> None:
    _save_raw({})


def reset_encounter_counts() -> int:
    data = _load_raw()
    for entry in data.values():
        entry["encounters"] = len(entry.get("sessions", [])) or 1
    _save_raw(data)
    return len(data)


def clear_all() -> None:
    _save_raw({})


def reset_encounter_counts() -> int:
    data = _load_raw()
    fixed = 0
    for entry in data.values():
        real = len(entry.get("sessions", []))
        if entry.get("encounters", 0) != real:
            entry["encounters"] = max(real, 1)
            fixed += 1
    _save_raw(data)
    return fixed


def display_name(entry: dict) -> str:
    names = entry.get("names") or []
    return names[0] if names else entry.get("steam_id64", "—")


def format_stamp(value: str) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return value