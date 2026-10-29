import json
import uuid
from datetime import datetime
from dataclasses import asdict
from launcher_config import BASE_DIR

FAST_GAMES_PATH = BASE_DIR / "fast_games.json"
MAX_GAMES = 50

def _load() -> list:
    if not FAST_GAMES_PATH.exists():
        return []
    try:
        data = json.loads(FAST_GAMES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []

def _save(games: list) -> None:
    try:
        FAST_GAMES_PATH.write_text(
            json.dumps(games[:MAX_GAMES], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

def get_fast_games() -> list:
    return _load()

def add_fast_game(snapshot) -> bool:
    """Добавляет игру, если в названии есть 'быстрая игра'. Название берется из hostname."""
    if not snapshot.hostname or "быстрая игра" not in snapshot.hostname.lower():
        return False

    games = _load()
    now = datetime.now().isoformat(timespec="seconds")
    
    # Проверка на дубликат: сравниваем с самой последней записью
    if games:
        last_game = games[0]
        if (last_game.get("hostname") == snapshot.hostname and 
            last_game.get("address") == snapshot.address):
            # Это та же игра, просто обновляем список игроков и время
            last_game["updated_at"] = now
            last_game["players"] = [asdict(p) for p in snapshot.players]
            _save(games)
            return True

    # Создаем новую запись
    new_game = {
        "id": uuid.uuid4().hex,
        "hostname": snapshot.hostname,
        "address": snapshot.address,
        "map_name": snapshot.map_name,
        "created_at": now,
        "updated_at": now,
        "players": [asdict(p) for p in snapshot.players]
    }
    games.insert(0, new_game)
    _save(games)
    return True

def migrate_from_players_db() -> int:
    """Парсит players.json и создает историю быстрых игр, если ее еще нет."""
    from players_db import get_all_players
    
    games_map = {}
    
    for player in get_all_players():
        for session in player.get("sessions", []):
            server_name = session.get("server", "")
            
            # Ищем только быстрые игры по текстовому названию
            if "быстрая игра" in server_name.lower():
                date_str = session.get("date", "")
                # Группируем по названию сервера и времени (с точностью до минуты)
                # чтобы собрать всех игроков в одну игру
                group_key = (server_name, date_str[:16])
                
                if group_key not in games_map:
                    games_map[group_key] = {
                        "id": f"migrated-{uuid.uuid4().hex[:8]}",
                        "hostname": server_name,
                        "address": "Неизвестно (из истории)",
                        "map_name": session.get("map", "—"),
                        "created_at": date_str,
                        "updated_at": date_str,
                        "players": []
                    }
                
                # Добавляем игрока в эту игру, если его там еще нет
                p_id = player.get("steam_id64", "")
                if p_id and not any(p.get("steam_id64") == p_id for p in games_map[group_key]["players"]):
                    games_map[group_key]["players"].append({
                        "name": session.get("name", player.get("names", ["—"])[0]),
                        "steam_id": player.get("steam_id", ""),
                        "steam_id64": p_id,
                        "connected": "—",
                        "ping": 0,
                        "loss": 0,
                        "state": "—"
                    })

    new_games = list(games_map.values())
    if new_games:
        new_games.sort(key=lambda x: x["created_at"], reverse=True)
        # Загружаем существующие, чтобы не потерять новые, и добавляем в конец
        existing = _load()
        existing_ids = {g["id"] for g in existing}
        existing_names = {g["hostname"] for g in existing}
        
        # Фильтруем дубликаты (по имени и времени)
        for ng in new_games:
            if not any(g["hostname"] == ng["hostname"] and g["created_at"] == ng["created_at"] for g in existing):
                existing.append(ng)
                
        existing.sort(key=lambda x: x["created_at"], reverse=True)
        _save(existing)
        return len(new_games)
    return 0