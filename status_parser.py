import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

STEAM64_BASE = 76561197960265728

HEADER_PATTERNS = {
    "hostname": re.compile(r"^hostname\s*:\s*(.+)$"),
    "version": re.compile(r"^version\s*:\s*(.+)$"),
    "address": re.compile(r"^udp/ip\s*:\s*(\S+)"),
    "server_steamid": re.compile(r"^steamid\s*:\s*(.+)$"),
    "map": re.compile(r"^map\s*:\s*(\S+)"),
    "uptime": re.compile(r"^uptime\s*:\s*(.+)$"),
    "players_line": re.compile(r"^players\s*:\s*(.+)$"),
}

PLAYER_RE = re.compile(
    r'^#\s*(?P<userid>\d+)\s+"(?P<name>.+)"\s+(?P<steamid>STEAM_\d:\d:\d+)\s+'
    r'(?P<connected>[\d:]+)\s+(?P<ping>\d+)\s+(?P<loss>\d+)\s+(?P<state>\w+)'
)


@dataclass
class StatusPlayer:
    userid: int
    name: str
    steam_id: str
    steam_id64: str
    connected: str
    ping: int
    loss: int
    state: str


@dataclass
class StatusSnapshot:
    hostname: str = ""
    version: str = ""
    address: str = ""
    server_steamid: str = ""
    map_name: str = ""
    uptime: str = ""
    players_line: str = ""
    players: List[StatusPlayer] = field(default_factory=list)
    captured_at: datetime = field(default_factory=datetime.now)

    @property
    def player_count(self) -> int:
        return len(self.players)


def legacy_to_steam_id64(legacy: str) -> str:
    match = re.match(r"^STEAM_\d:(\d):(\d+)$", legacy.strip())
    if not match:
        return ""
    y = int(match.group(1))
    z = int(match.group(2))
    return str(STEAM64_BASE + z * 2 + y)


def parse_status_text(text: str) -> Optional[StatusSnapshot]:
    collector = StatusCollector()
    for line in text.splitlines():
        snapshot = collector.feed(line)
        if snapshot:
            return snapshot
    return collector.flush()


class StatusCollector:

    def __init__(self):
        self._pending: Optional[StatusSnapshot] = None
        self._saw_players = False

    def feed(self, line: str) -> Optional[StatusSnapshot]:
        stripped = line.strip()

        header_match = HEADER_PATTERNS["hostname"].match(stripped)
        if header_match:
            finished = self.flush()
            self._pending = StatusSnapshot(hostname=header_match.group(1).strip())
            self._saw_players = False
            return finished

        if self._pending is None:
            return None

        for key, pattern in HEADER_PATTERNS.items():
            if key == "hostname":
                continue
            m = pattern.match(stripped)
            if m:
                value = m.group(1).strip()
                if key == "players_line":
                    self._pending.players_line = value
                elif key == "map":
                    self._pending.map_name = value
                else:
                    setattr(self._pending, key, value)
                return None

        player_match = PLAYER_RE.match(stripped)
        if player_match:
            self._saw_players = True
            legacy = player_match.group("steamid")
            self._pending.players.append(StatusPlayer(
                userid=int(player_match.group("userid")),
                name=player_match.group("name"),
                steam_id=legacy,
                steam_id64=legacy_to_steam_id64(legacy),
                connected=player_match.group("connected"),
                ping=int(player_match.group("ping")),
                loss=int(player_match.group("loss")),
                state=player_match.group("state"),
            ))
            return None

        if stripped.startswith("#"):
            return None

        if self._saw_players:
            return self.flush()

        return None

    def flush(self) -> Optional[StatusSnapshot]:
        if self._pending is not None and self._pending.players:
            snapshot = self._pending
            self._pending = None
            self._saw_players = False
            return snapshot
        return None

    def has_pending_players(self) -> bool:
        return self._pending is not None and bool(self._pending.players)