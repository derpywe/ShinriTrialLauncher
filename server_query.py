import a2s
from dataclasses import dataclass
from typing import Optional


@dataclass
class ServerStatus:
    ip: str
    port: int
    fallback_name: str
    online: bool = False
    queried: bool = False
    name: str = ""
    map_name: str = ""
    players: int = 0
    max_players: int = 0
    ping_ms: Optional[float] = None
    error: str = ""

    @property
    def address(self):
        return (self.ip, self.port)

    @property
    def display_name(self) -> str:
        if not self.queried:
            return "Загрузка..."
        if self.online and self.name:
            return self.name
        return self.fallback_name

    @property
    def players_str(self) -> str:
        if not self.online:
            return "—"
        return f"{self.players}/{self.max_players}"


def query_server(ip: str, port: int, fallback_name: str, timeout: float = 3.0, retries: int = 2) -> ServerStatus:
    status = ServerStatus(ip=ip, port=port, fallback_name=fallback_name)
    last_error = ""
    for attempt in range(retries):
        try:
            info = a2s.info((ip, port), timeout=timeout)
            status.online = True
            status.name = info.server_name
            status.map_name = info.map_name
            status.players = info.player_count
            status.max_players = info.max_players
            status.ping_ms = round(info.ping * 1000, 1) if hasattr(info, "ping") and info.ping else None
            status.error = ""
            break
        except Exception as e:
            status.online = False
            last_error = f"{type(e).__name__}: {e}"
            status.error = last_error
    status.queried = True
    return status