"""Поиск игрока по id на shinrireviews: ник, id, аватарка, история ников, отзывы."""
import datetime
import json
import os
import sys
import textwrap
import time
import urllib.error

import shinri_reviews_api as api
from launcher_config import BASE_DIR

# Теперь кэш сохраняется в папку с конфигом (AppData), а не рядом с .exe
AVATAR_CACHE = BASE_DIR / "avatars_cache.json"
AVATAR_MAX_AGE = 24 * 3600
_avatars = None


def avatar_map():
    """{avatarGameId: url}, с файловым кэшем на сутки."""
    global _avatars
    if _avatars is not None:
        return _avatars
    
    # os.path.exists и os.path.getmtime отлично работают с объектами Path
    if os.path.exists(AVATAR_CACHE) and time.time() - os.path.getmtime(AVATAR_CACHE) < AVATAR_MAX_AGE:
        try:
            with open(AVATAR_CACHE, encoding="utf-8") as f:
                _avatars = json.load(f)
                return _avatars
        except (OSError, ValueError):
            pass
            
    try:
        _avatars = api.discover_avatar_urls()
    except Exception:
        _avatars = {}
        
    if _avatars:
        try:
            with open(AVATAR_CACHE, "w", encoding="utf-8") as f:
                json.dump(_avatars, f, ensure_ascii=False)
        except OSError:
            pass
            
    return _avatars


def _date(r):
    for key in ("createdAt", "created_at", "date", "time", "ts"):
        v = r.get(key)
        if isinstance(v, (int, float)) and v > 0:
            v = v / 1000 if v > 1e11 else v
            return datetime.datetime.fromtimestamp(v).strftime("%Y-%m-%d")
        if isinstance(v, str) and v:
            return v[:10]
    return None


def lookup(uid):
    """Собирает всё об игроке. Возвращает dict или None, если игрока нет."""
    uid = int(uid)
    try:
        p = api.get_player(uid)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    if not p:
        return None
    try:
        hist = api.nick_history(uid)
    except Exception:
        hist = {"current": "", "history": []}
    try:
        rev = api.reviews(uid)
    except Exception:
        rev = {}
    stats = (rev or {}).get("stats") or {}
    avatar_id = p.get("avatarGameId")
    return {
        "id": uid,
        "name": p.get("name"),
        "profile_url": api.profile_url(uid),
        "avatarGameId": avatar_id,
        "avatar_url": avatar_map().get((avatar_id or "").lower()),
        "nick_history": (hist or {}).get("history") or [],
        "reviews_count": stats.get("count", 0),
        "reviews_avg": stats.get("avg"),
        "reviews": (rev or {}).get("reviews") or [],
        "raw": {"player": p, "nick_history": hist, "reviews": rev},
    }


def show(info, show_all=False, raw=False):
    if info is None:
        print("Игрок с таким id не найден.\n")
        return
    print("=" * 60)
    print(f"Ник:       {info['name']}")
    print(f"ID:        {info['id']}")
    print(f"Профиль:   {info['profile_url']}")
    print(f"Аватарка:  {info['avatarGameId'] or '-'}  {info['avatar_url'] or '(ссылка не найдена)'}")
    hist = info["nick_history"]
    print(f"Прошлые ники: {json.dumps(hist, ensure_ascii=False) if hist else 'нет'}")
    avg = info["reviews_avg"]
    print(f"Отзывы:    {info['reviews_count']} шт., средняя оценка: {avg if avg is not None else '-'}")
    revs = info["reviews"]
    limit = len(revs) if show_all else 15
    for r in revs[:limit]:
        author = r.get("authorNickname") or r.get("authorGameName") or "Аноним"
        mark = " [проверен]" if r.get("authorVerified") else ""
        date = _date(r)
        head = f"- {r.get('rating', '?')}/5  {author}{mark}"
        if date:
            head += f"  {date}"
        if r.get("likes"):
            head += f"  👍{r['likes']}"
        print(head)
        text = str(r.get("text") or "").strip()
        if text:
            print(textwrap.indent(textwrap.fill(text, 56), "    "))
    if len(revs) > limit:
        print(f"... и ещё {len(revs) - limit} (запусти с --all)")
    if raw:
        print("\nСЫРЫЕ ОТВЕТЫ:")
        print(json.dumps(info["raw"], ensure_ascii=False, indent=2)[:6000])
    print()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    show_all, raw = "--all" in flags, "--raw" in flags
    if args:
        ids = args
    else:
        ids = None
    while True:
        uid = ids.pop(0) if ids else (input("id игрока (пусто = выход): ").strip() if ids is None else "")
        if not uid:
            break
        uid = uid.lstrip("#")
        if not uid.isdigit():
            print("Нужен числовой id, например 3865.\n")
            continue
        try:
            show(lookup(uid), show_all, raw)
        except Exception as exc:
            print(f"Ошибка запроса: {exc}\n")


if __name__ == "__main__":
    main()