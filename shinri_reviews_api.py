"""Минимальный клиент к внутреннему API shinrireviews.com (/rapi).

Эндпоинты и формат ответов взяты из открытого репозитория SiderSq/shinrichecker
(shinri_ranker/client.py). Официальной документации у сайта нет, поэтому формат
может измениться в любой момент: всё читается через .get() и не падает на
неожиданных полях.

Не проверено вживую: из моей песочницы shinrireviews.com недоступен.
"""
import json
import re
import time
import urllib.parse
import urllib.request

BASE = "https://shinrireviews.com"
API = BASE + "/rapi"
HEADERS = {
    "User-Agent": "ShinriTrialLauncher/1.0",
    "Accept": "application/json, text/plain, */*",
    "Referer": BASE + "/",
    "Origin": BASE,
}
_last_call = 0.0


def _get(url, as_json=True, min_gap=0.5):
    """GET с паузой между запросами, чтобы не долбить чужой сервер."""
    global _last_call
    wait = min_gap - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode("utf-8", "replace")
    return json.loads(body) if as_json else body


def _unwrap(data):
    """Если API вернул словарь-обёртку, достаём из неё список."""
    if isinstance(data, dict):
        for key in ("players", "data", "items", "results", "list"):
            if isinstance(data.get(key), list):
                return data[key]
    return data


def _norm(item):
    """Приводит запись игрока к {"id", "name", "avatarGameId"} или None."""
    if isinstance(item, list) and len(item) >= 2:
        return {"id": int(item[0]), "name": str(item[1]),
                "avatarGameId": item[4] if len(item) > 4 else None}
    if isinstance(item, dict):
        pid = item.get("id", item.get("playerId"))
        name = item.get("name", item.get("playerName", item.get("nickname")))
        if pid is not None and name is not None:
            return {"id": int(pid), "name": str(name),
                    "avatarGameId": item.get("avatarGameId")}
    return None


def debug_players(limit=400):
    """Печатает, что реально вернул /players: тип, размер и начало ответа."""
    raw = _get(f"{API}/players", as_json=False)
    print("длина ответа:", len(raw))
    print(raw[:limit])


def load_directory():
    """Каталог игроков: {id: {"id", "name", "avatarGameId"}}.

    Тяжёлый запрос: скачай один раз и сохрани в файл, не дёргай при каждом старте.
    Пробуем /players (список или обёртка), при пустом результате берём /ratings.
    """
    out = {}
    try:
        for item in _unwrap(_get(f"{API}/players")) or []:
            rec = _norm(item)
            if rec:
                out[rec["id"]] = rec
    except Exception as exc:
        print("/players не разобрался:", exc)
    if not out:
        print("каталог /players пуст, беру /ratings (только игроки с оценками)")
        for item in _unwrap(load_ratings()) or []:
            rec = _norm(item)
            if rec:
                out[rec["id"]] = rec
    return out


def load_ratings():
    """Игроки с оценками: [{playerId, playerName, avg, count, last, avatarGameId}]."""
    data = _get(f"{API}/ratings")
    return data if isinstance(data, list) else []


def get_player(player_id):
    """Один профиль: {"id", "name", "avatarGameId", ...}."""
    return _get(f"{API}/players/{int(player_id)}")


def find_by_name(name):
    q = urllib.parse.quote(name)
    return _get(f"{API}/players/by-name?name={q}")


def search(query):
    return _get(f"{API}/players/search?q={urllib.parse.quote(query)}")


def nick_history(player_id):
    """{"current": "...", "history": [...]}"""
    return _get(f"{API}/players/{int(player_id)}/nick-history")


def reviews(player_id):
    """{"stats": {"count", "avg"}, "reviews": [...]}"""
    return _get(f"{API}/reviews/{int(player_id)}")


def profile_url(player_id):
    return f"{BASE}/p/{int(player_id)}"


def discover_avatar_urls():
    """Пытается найти картинки аватарок в сборке сайта: {avatarGameId: url}.

    avatarGameId (например "akane_1") в API не ссылка. Картинки лежат в
    /assets/<id>-<хэш>.png, а хэш меняется при каждой пересборке фронтенда,
    поэтому статичный список быстро устаревает. Здесь ищем актуальные имена
    в index.html и подключённых JS-файлах. Это best-effort: если картинки
    подгружаются отдельными чанками, часть id может не найтись.
    """
    pat = re.compile(r"assets/([A-Za-z0-9_\-]+?)-([A-Za-z0-9_\-]{8})\.png")
    html = _get(BASE + "/", as_json=False)
    pages = [html]
    for src in set(re.findall(r'(?:src|href)="(/assets/[^"]+\.js)"', html)):
        try:
            pages.append(_get(BASE + src, as_json=False))
        except Exception:
            pass
    found = {}
    for text in pages:
        for m in pat.finditer(text):
            found[m.group(1).lower()] = f"{BASE}/assets/{m.group(1)}-{m.group(2)}.png"
    return found


if __name__ == "__main__":
    d = load_directory()
    print("игроков в каталоге:", len(d))
    if not d:
        debug_players()
    avatars = discover_avatar_urls()
    print("аватарок найдено:", len(avatars))
    for p in list(d.values())[:5]:
        print(p["id"], p["name"], avatars.get((p["avatarGameId"] or "").lower()))