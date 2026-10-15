from dataclasses import dataclass
from typing import List, Optional

import requests

GMOD_APPID = "4000"

FRIEND_LIST_URL = "https://api.steampowered.com/ISteamUser/GetFriendList/v1/"
PLAYER_SUMMARIES_URL = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
RESOLVE_VANITY_URL = "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/"

API_KEY_PAGE = "https://steamcommunity.com/dev/apikey"


class SteamApiError(Exception):
    pass


def _request(url: str, params: dict, timeout: float):
    api_key = params.get("key", "")
    try:
        resp = requests.get(url, params=params, timeout=timeout)
    except requests.RequestException as e:
        raise SteamApiError(_redact(str(e), api_key))

    if resp.status_code == 401:
        raise SteamApiError(
            "Steam отклонил запрос (401). Обычно причина одна из двух: "
            "неверный или отозванный API-ключ, либо список друзей скрыт в "
            "настройках приватности профиля — открой Steam → Профиль → "
            "Редактировать → Приватность и поставь «Друзья» на «Открытый»."
        )
    if resp.status_code == 403:
        raise SteamApiError("Steam отклонил запрос (403): нет доступа к этим данным.")

    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise SteamApiError(_redact(str(e), api_key))
    return resp


def _redact(message: str, api_key: str) -> str:
    if api_key:
        message = message.replace(api_key, "***")
    return message


@dataclass
class FriendInfo:
    steam_id: str
    name: str
    profile_url: str
    game_name: str
    server_ip: Optional[str]
    server_port: Optional[int]
    server_name: str = ""

    @property
    def server_address(self) -> str:
        if self.server_ip and self.server_port:
            return f"{self.server_ip}:{self.server_port}"
        return ""


def resolve_steam_id(api_key: str, raw: str, timeout: float = 6.0) -> str:
    raw = raw.strip().rstrip("/")
    if not raw:
        raise ValueError("Не указан SteamID или ссылка на профиль")

    if "/profiles/" in raw:
        raw = raw.split("/profiles/")[-1].split("/")[0]
    elif "/id/" in raw:
        raw = raw.split("/id/")[-1].split("/")[0]

    if raw.isdigit() and len(raw) >= 17:
        return raw

    resp = _request(
        RESOLVE_VANITY_URL,
        {"key": api_key, "vanityurl": raw},
        timeout,
    )
    data = resp.json().get("response", {})
    if data.get("success") != 1 or not data.get("steamid"):
        raise ValueError(f"Не удалось определить SteamID по «{raw}»")
    return data["steamid"]


def _fetch_summaries(api_key: str, steam_ids: List[str], timeout: float) -> List[dict]:
    players: List[dict] = []
    for i in range(0, len(steam_ids), 100):
        chunk = steam_ids[i:i + 100]
        resp = _request(
            PLAYER_SUMMARIES_URL,
            {"key": api_key, "steamids": ",".join(chunk)},
            timeout,
        )
        players.extend(resp.json().get("response", {}).get("players", []))
    return players


def _parse_server(player: dict):
    raw = player.get("gameserverip", "")
    if ":" not in raw:
        return None, None
    ip, _, port_str = raw.rpartition(":")
    try:
        return ip, int(port_str)
    except ValueError:
        return None, None


def get_friends_in_gmod(api_key: str, steam_id: str, timeout: float = 8.0) -> List[FriendInfo]:
    resp = _request(
        FRIEND_LIST_URL,
        {"key": api_key, "steamid": steam_id, "relationship": "friend"},
        timeout,
    )
    friends = resp.json().get("friendslist", {}).get("friends", [])
    friend_ids = [f["steamid"] for f in friends if f.get("steamid")]
    if not friend_ids:
        return []

    result: List[FriendInfo] = []
    for player in _fetch_summaries(api_key, friend_ids, timeout):
        if str(player.get("gameid", "")) != GMOD_APPID:
            continue
        ip, port = _parse_server(player)
        result.append(FriendInfo(
            steam_id=str(player.get("steamid", "")),
            name=player.get("personaname", "—"),
            profile_url=player.get("profileurl", ""),
            game_name=player.get("gameextrainfo", "Garry's Mod"),
            server_ip=ip,
            server_port=port,
        ))

    result.sort(key=lambda f: (f.server_address == "", f.name.lower()))
    return result