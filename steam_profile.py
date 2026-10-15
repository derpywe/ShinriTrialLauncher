from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from steam_friends import (
    PLAYER_SUMMARIES_URL,
    _fetch_summaries,
    _request,
    resolve_steam_id,
)

PLAYER_BANS_URL = "https://api.steampowered.com/ISteamUser/GetPlayerBans/v1/"
STEAM_LEVEL_URL = "https://api.steampowered.com/IPlayerService/GetSteamLevel/v1/"
OWNED_GAMES_URL = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"

GMOD_APPID = 4000
STEAM64_BASE = 76561197960265728

PERSONA_STATES = {
    0: "Не в сети",
    1: "В сети",
    2: "Занят",
    3: "Нет на месте",
    4: "Спит",
    5: "Готов меняться",
    6: "Готов играть",
}

VISIBILITY = {
    1: "Скрытый",
    2: "Только друзьям",
    3: "Открытый",
}


@dataclass
class SteamProfile:
    steam_id64: str
    steam_id: str
    steam_id3: str
    name: str
    real_name: str
    profile_url: str
    avatar_url: str
    visibility: str
    state: str
    country: str
    created: str
    last_logoff: str
    current_game: str
    current_server: str
    level: Optional[int]
    vac_bans: int
    game_bans: int
    days_since_ban: Optional[int]
    community_banned: bool
    trade_ban: str
    games_count: Optional[int]
    gmod_hours: Optional[float]
    extra_notes: List[str] = field(default_factory=list)


def steam_id64_to_legacy(steam_id64: str) -> Tuple[str, str]:
    try:
        account_id = int(steam_id64) - STEAM64_BASE
    except ValueError:
        return "—", "—"
    if account_id < 0:
        return "—", "—"
    return f"STEAM_0:{account_id % 2}:{account_id // 2}", f"[U:1:{account_id}]"


def _format_unix(value) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    except (ValueError, OSError, OverflowError):
        return "—"


def _safe_get(url: str, params: dict, timeout: float):
    try:
        return _request(url, params, timeout).json()
    except Exception:
        return None


def lookup_profile(api_key: str, raw_input: str, timeout: float = 8.0) -> SteamProfile:
    steam_id64 = resolve_steam_id(api_key, raw_input)
    notes: List[str] = []

    summaries = _request(
        PLAYER_SUMMARIES_URL, {"key": api_key, "steamids": steam_id64}, timeout
    ).json()
    players = summaries.get("response", {}).get("players", [])
    if not players:
        raise ValueError("Профиль с таким SteamID не найден")
    p = players[0]

    visibility_code = p.get("communityvisibilitystate", 1)
    visibility = VISIBILITY.get(visibility_code, "Неизвестно")
    if visibility_code != 3:
        notes.append(
            "Профиль закрыт или виден только друзьям — часть данных Steam не отдаёт."
        )

    bans_data = _safe_get(PLAYER_BANS_URL, {"key": api_key, "steamids": steam_id64}, timeout)
    ban = (bans_data or {}).get("players", [{}])[0] if bans_data else {}

    level_data = _safe_get(STEAM_LEVEL_URL, {"key": api_key, "steamid": steam_id64}, timeout)
    level = (level_data or {}).get("response", {}).get("player_level")

    games_data = _safe_get(
        OWNED_GAMES_URL,
        {"key": api_key, "steamid": steam_id64, "include_played_free_games": 1},
        timeout,
    )
    games_response = (games_data or {}).get("response", {}) if games_data else {}
    games_count = games_response.get("game_count")
    gmod_hours = None
    for game in games_response.get("games", []):
        if game.get("appid") == GMOD_APPID:
            gmod_hours = round(game.get("playtime_forever", 0) / 60, 1)
            break
    if games_count is None:
        notes.append("Список игр скрыт настройками приватности.")

    server = p.get("gameserverip", "")
    legacy, id3 = steam_id64_to_legacy(steam_id64)

    return SteamProfile(
        steam_id64=steam_id64,
        steam_id=legacy,
        steam_id3=id3,
        name=p.get("personaname", "—"),
        real_name=p.get("realname", "") or "—",
        profile_url=p.get("profileurl", ""),
        avatar_url=p.get("avatarfull", ""),
        visibility=visibility,
        state=PERSONA_STATES.get(p.get("personastate", 0), "—"),
        country=p.get("loccountrycode", "") or "—",
        created=_format_unix(p.get("timecreated")),
        last_logoff=_format_unix(p.get("lastlogoff")),
        current_game=p.get("gameextrainfo", "") or "—",
        current_server=server or "—",
        level=level,
        vac_bans=ban.get("NumberOfVACBans", 0),
        game_bans=ban.get("NumberOfGameBans", 0),
        days_since_ban=ban.get("DaysSinceLastBan"),
        community_banned=bool(ban.get("CommunityBanned", False)),
        trade_ban=ban.get("EconomyBan", "none"),
        games_count=games_count,
        gmod_hours=gmod_hours,
        extra_notes=notes,
    )


def profile_to_groups(profile: SteamProfile) -> List[Tuple[str, List[Tuple[str, str]]]]:
    bans = []
    if profile.vac_bans:
        bans.append(f"VAC: {profile.vac_bans}")
    if profile.game_bans:
        bans.append(f"игровых: {profile.game_bans}")
    if profile.community_banned:
        bans.append("бан сообщества")
    if profile.trade_ban and profile.trade_ban != "none":
        bans.append(f"торговля: {profile.trade_ban}")
    bans_text = ", ".join(bans) if bans else "нет"
    if bans and profile.days_since_ban is not None:
        bans_text += f" (последний {profile.days_since_ban} дн. назад)"

    return [
        ("Идентификаторы", [
            ("SteamID64", profile.steam_id64),
            ("SteamID", profile.steam_id),
            ("SteamID3", profile.steam_id3),
            ("Ссылка", profile.profile_url or "—"),
        ]),
        ("Сейчас", [
            ("Статус", profile.state),
            ("Играет в", profile.current_game),
            ("Игровой сервер", profile.current_server),
        ]),
        ("Аккаунт", [
            ("Настоящее имя", profile.real_name),
            ("Уровень Steam", str(profile.level) if profile.level is not None else "—"),
            ("Создан", profile.created),
            ("Последний выход", profile.last_logoff),
            ("Страна", profile.country),
            ("Видимость профиля", profile.visibility),
        ]),
        ("Игры", [
            ("Игр на аккаунте", str(profile.games_count) if profile.games_count is not None else "—"),
            ("Часов в GMod", f"{profile.gmod_hours}" if profile.gmod_hours is not None else "—"),
        ]),
        ("Ограничения", [
            ("Баны", bans_text),
        ]),
    ]


def fetch_summaries_map(api_key: str, steam_ids: List[str], timeout: float = 8.0) -> dict:
    result = {}
    for player in _fetch_summaries(api_key, [i for i in steam_ids if i], timeout):
        result[str(player.get("steamid", ""))] = {
            "name": player.get("personaname", ""),
            "avatar": player.get("avatarmedium", "") or player.get("avatar", ""),
            "profile_url": player.get("profileurl", ""),
        }
    return result


def fetch_avatar(url: str, timeout: float = 6.0) -> Optional[bytes]:
    if not url:
        return None
    try:
        import requests
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.content
    except Exception:
        return None


def profile_to_rows(profile: SteamProfile) -> List[Tuple[str, str]]:
    bans = []
    if profile.vac_bans:
        bans.append(f"VAC: {profile.vac_bans}")
    if profile.game_bans:
        bans.append(f"игровых: {profile.game_bans}")
    if profile.community_banned:
        bans.append("бан сообщества")
    if profile.trade_ban and profile.trade_ban != "none":
        bans.append(f"торговля: {profile.trade_ban}")
    bans_text = ", ".join(bans) if bans else "нет"
    if bans and profile.days_since_ban is not None:
        bans_text += f" (последний {profile.days_since_ban} дн. назад)"

    return [
        ("Ник", profile.name),
        ("Настоящее имя", profile.real_name),
        ("SteamID64", profile.steam_id64),
        ("SteamID", profile.steam_id),
        ("SteamID3", profile.steam_id3),
        ("Статус", profile.state),
        ("Сейчас играет", profile.current_game),
        ("Игровой сервер", profile.current_server),
        ("Уровень Steam", str(profile.level) if profile.level is not None else "—"),
        ("Аккаунт создан", profile.created),
        ("Последний выход", profile.last_logoff),
        ("Страна", profile.country),
        ("Видимость профиля", profile.visibility),
        ("Игр на аккаунте", str(profile.games_count) if profile.games_count is not None else "—"),
        ("Часов в GMod", f"{profile.gmod_hours}" if profile.gmod_hours is not None else "—"),
        ("Баны", bans_text),
    ]