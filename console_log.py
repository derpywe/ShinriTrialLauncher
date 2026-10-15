import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

COMMON_GMOD_PATHS = [
    r"C:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod",
    r"C:\Program Files\Steam\steamapps\common\GarrysMod\garrysmod",
    r"D:\Steam\steamapps\common\GarrysMod\garrysmod",
    r"D:\SteamLibrary\steamapps\common\GarrysMod\garrysmod",
    r"E:\Steam\steamapps\common\GarrysMod\garrysmod",
    r"E:\SteamLibrary\steamapps\common\GarrysMod\garrysmod",
]

CONSOLE_LOG_NAME = "console.log"


def find_console_log() -> Optional[Path]:
    for base in COMMON_GMOD_PATHS:
        candidate = Path(base) / CONSOLE_LOG_NAME
        if candidate.exists():
            return candidate
    return None


def find_gmod_dirs() -> List[Path]:
    return [Path(p) for p in COMMON_GMOD_PATHS if Path(p).exists()]


def is_gmod_running() -> bool:
    # GMod, как и все моды/игры на Source-движке, запускается процессом
    # hl2.exe — отдельного gmod.exe в системе нет, поэтому проверка только
    # по "gmod.exe" почти никогда не срабатывала. Проверяем оба имени —
    # на случай нестандартных лаунчеров/сборок.
    try:
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            result = subprocess.run(
                ["tasklist", "/NH"],
                capture_output=True,
                text=True,
                startupinfo=startupinfo,
                timeout=5,
            )
            output = result.stdout.lower()
            return "hl2.exe" in output or "gmod.exe" in output
        result = subprocess.run(
            ["pgrep", "-x", "hl2_linux"], capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            return True
        result = subprocess.run(
            ["pgrep", "-x", "gmod"], capture_output=True, text=True, timeout=5
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


class ConsoleLogWatcher:

    def __init__(self, log_path: Path, poll_interval: float = 0.5):
        self.log_path = Path(log_path)
        self.poll_interval = poll_interval
        self._stop_flag = False
        self._thread: Optional[threading.Thread] = None
        self._position = 0

        self.on_line: Optional[Callable[[str], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None

    def start(self, from_beginning: bool = False, tail_lines: int = 200) -> None:
        if not self.log_path.exists():
            raise FileNotFoundError(f"Файл не найден: {self.log_path}")

        if from_beginning:
            self._position = 0
        else:
            self._position = self._compute_tail_position(tail_lines)

        self._stop_flag = False
        self._thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()

    def _compute_tail_position(self, tail_lines: int) -> int:
        try:
            size = self.log_path.stat().st_size
            chunk = min(size, 64 * 1024)
            with open(self.log_path, "rb") as f:
                f.seek(size - chunk)
                data = f.read(chunk)
            lines = data.split(b"\n")
            if len(lines) <= tail_lines:
                return max(0, size - chunk)
            keep = b"\n".join(lines[-tail_lines:])
            return size - len(keep)
        except Exception:
            return 0

    def _watch_loop(self):
        while not self._stop_flag:
            try:
                if not self.log_path.exists():
                    time.sleep(self.poll_interval)
                    continue

                size = self.log_path.stat().st_size
                if size < self._position:
                    self._position = 0

                if size > self._position:
                    with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(self._position)
                        new_data = f.read()
                        self._position = f.tell()

                    for line in new_data.splitlines():
                        if self._stop_flag:
                            return
                        if line.strip() and self.on_line:
                            self.on_line(line)
            except Exception as e:
                if self.on_error and not self._stop_flag:
                    self.on_error(str(e))

            time.sleep(self.poll_interval)

    def stop(self):
        self._stop_flag = True

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop_flag