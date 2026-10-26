import sys
import os
import re
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import (
    Qt, QTimer, QObject, pyqtSignal, QUrl, QPoint, QSize, QRectF,
)
from PyQt6.QtGui import (
    QColor, QFont, QIcon, QPixmap, QAction, QPainter,
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QTableWidget, QTableWidgetItem, QPushButton, QLabel,
    QHeaderView, QAbstractItemView, QFrame, QScrollArea, QComboBox, QDialog,
    QCheckBox, QSystemTrayIcon, QMenu, QLineEdit, QPlainTextEdit, QFileDialog,
    QGridLayout, QMessageBox
)

from servers import LOBBIES, ENG_LOBBIES
from server_query import query_server, ServerStatus
from console_log import ConsoleLogWatcher, find_console_log, find_gmod_dirs, is_gmod_running
from steam_friends import get_friends_in_gmod, resolve_steam_id, API_KEY_PAGE
from steam_profile import lookup_profile, profile_to_groups, fetch_avatar, fetch_summaries_map
from status_parser import StatusCollector
from players_db import (
    record_encounter, get_all_players, set_comment, get_comments_map, get_player,
    clear_all, reset_encounter_counts,
    display_name as player_display_name, format_stamp,
)
from launcher_config import load_config, save_config, add_recent_server, get_recent_servers
from theme import THEME_QSS, HOPE_GOLD, ACCENT, ASH, BONE
from version import __version__
from update_checker import check_for_update
from notifications import (
    fetch_notifications, has_unread, mark_all_read,
)

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
ICON_ICO = ASSETS_DIR / "icon.ico"
ICON_PNG = ASSETS_DIR / "icon.png"


REFRESH_INTERVAL_MS = 5_000
WATCH_REFRESH_INTERVAL_MS = 1_000
FRIENDS_REFRESH_INTERVAL_MS = 15_000
MAX_CONSOLE_ENTRIES = 5000

CONNECTED_RE = re.compile(
    r"Connect(?:ed|ing) to\s+(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})",
    re.IGNORECASE,
)


def _format_size(num_bytes: int) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if num_bytes < 1024 or unit == "ГБ":
            if unit == "Б":
                return f"{num_bytes} {unit}"
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} ГБ"


def _make_bell_icon(color: str, size: int = 20) -> QIcon:
    """Рисует колокольчик вручную, чтобы не зависеть от наличия глифа в шрифте."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    w = h = float(size)
    # Купол — верхняя половина овала, плоское основание снизу
    painter.drawPie(QRectF(w * 0.20, h * 0.10, w * 0.60, h * 0.55), 0, 180 * 16)
    # Основание колокольчика
    painter.drawRect(QRectF(w * 0.17, h * 0.60, w * 0.66, h * 0.09))
    # Язычок
    painter.drawEllipse(QRectF(w * 0.42, h * 0.74, w * 0.16, h * 0.16))
    painter.end()
    return QIcon(pixmap)


class _ConnectionEvents(QObject):
    """
    Единая точка, через которую проходит любое подключение, инициированное
    из лаунчера (Лобби, ENG Lobby, автоочередь, вкладка друзей). Вкладка
    «Последние сервера» подписывается на неё, чтобы обновляться сразу же,
    а не только когда её саму открывают/обновляют вручную.
    """
    connected = pyqtSignal(str, int, str)


connection_events = _ConnectionEvents()


def connect_to_server(ip: str, port: int, name: str = ""):
    webbrowser.open(f"steam://connect/{ip}:{port}")
    display_name = name or f"{ip}:{port}"
    config = load_config()
    config["last_server"] = {"ip": ip, "port": port, "name": display_name}
    save_config(config)
    add_recent_server(ip, port, display_name)
    connection_events.connected.emit(ip, port, display_name)


def is_server_full(status: Optional[ServerStatus]) -> bool:
    return bool(
        status
        and status.online
        and status.max_players > 0
        and status.players >= status.max_players
    )


def is_server_unavailable(status: Optional[ServerStatus]) -> bool:
    return bool(status and status.queried and not status.online)


class ServerTab(QWidget):

    COLUMNS = ["Название", "Карта", "Игроки", "Пинг", "Статус", ""]


    result_ready = pyqtSignal(int, object)
    queue_connected = pyqtSignal(str, str)

    def __init__(self, server_defs, empty_hint: str, parent=None):
        super().__init__(parent)

        self.server_defs = server_defs
        self.statuses: dict[tuple, ServerStatus] = {
            (ip, port): ServerStatus(ip=ip, port=port, fallback_name=name)
            for ip, port, name in server_defs
        }
        self.executor = ThreadPoolExecutor(max_workers=8)
        self.result_ready.connect(self._apply_row)
        self.buttons: dict[int, QPushButton] = {}
        self.row_for_key: dict[tuple, int] = {}
        self.watching: set[tuple] = set()
        self.watch_timer = QTimer(self)
        self.watch_timer.setInterval(WATCH_REFRESH_INTERVAL_MS)
        self.watch_timer.timeout.connect(self._refresh_watched)
        self._closed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        top_bar = QHBoxLayout()

        saved_filters = load_config().get("server_filters", {})

        self.hide_offline_checkbox = QCheckBox("Скрыть неактивные сервера")
        self.hide_offline_checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hide_offline_checkbox.blockSignals(True)
        self.hide_offline_checkbox.setChecked(bool(saved_filters.get("hide_offline", False)))
        self.hide_offline_checkbox.blockSignals(False)
        self.hide_offline_checkbox.stateChanged.connect(self._on_filter_toggled)

        self.hide_full_checkbox = QCheckBox("Скрыть полные сервера")
        self.hide_full_checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hide_full_checkbox.blockSignals(True)
        self.hide_full_checkbox.setChecked(bool(saved_filters.get("hide_full", False)))
        self.hide_full_checkbox.blockSignals(False)
        self.hide_full_checkbox.stateChanged.connect(self._on_filter_toggled)

        refresh_btn = QPushButton("⟳  Обновить")
        refresh_btn.setObjectName("RefreshButton")
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.clicked.connect(self.refresh)

        top_bar.addWidget(self.hide_offline_checkbox)
        top_bar.addWidget(self.hide_full_checkbox)
        top_bar.addStretch()
        top_bar.addWidget(refresh_btn)
        layout.addLayout(top_bar)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(40)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionsMovable(False)
        for col, width in ((1, 130), (2, 90), (3, 90), (4, 130), (5, 165)):
            self.table.setColumnWidth(col, width)
        layout.addWidget(self.table)

        self.hint_label = None
        if not server_defs:
            self.hint_label = QLabel(empty_hint)
            self.hint_label.setObjectName("HintLabel")
            self.hint_label.setWordWrap(True)
            layout.addWidget(self.hint_label)

        self._build_rows()
        QTimer.singleShot(0, self.refresh)

    def _build_rows(self):
        if self.hint_label is not None:
            self.hint_label.setVisible(len(self.server_defs) == 0)
        self.table.setRowCount(len(self.server_defs))
        self.row_for_key = {}
        for row, (ip, port, name) in enumerate(self.server_defs):
            self.row_for_key[(ip, port)] = row
            self.table.setItem(row, 0, QTableWidgetItem("Загрузка..."))
            self.table.setItem(row, 1, QTableWidgetItem("—"))
            self.table.setItem(row, 2, QTableWidgetItem("—"))
            self.table.setItem(row, 3, QTableWidgetItem("—"))
            self.table.setItem(row, 4, QTableWidgetItem("Опрос..."))

            btn = QPushButton("Подключиться")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(28)
            btn.clicked.connect(lambda _, ip=ip, port=port: self._handle_connect_click(ip, port))
            self.table.setCellWidget(row, 5, btn)
            self.buttons[row] = btn

        self._apply_filters()

    def refresh(self):
        if self._closed:
            return
        for row, (ip, port, name) in enumerate(self.server_defs):
            future = self.executor.submit(query_server, ip, port, name)
            future.add_done_callback(
                lambda f, row=row, ip=ip, port=port: self._on_future_done(f, row, ip, port)
            )

    def _on_future_done(self, future, row: int, ip: str, port: int):
        if future.cancelled():
            return
        try:
            status = future.result()
        except Exception:
            return
        self._on_result(row, ip, port, status)

    def _handle_connect_click(self, ip: str, port: int):
        key = (ip, port)
        status = self.statuses.get(key)

        if key in self.watching:
            self._stop_watching(key)
            return

        if is_server_full(status) or is_server_unavailable(status):
            self._start_watching(key)
            return

        name = status.display_name if status else f"{ip}:{port}"
        connect_to_server(ip, port, name)

        self.refresh()

    def _start_watching(self, key: tuple):
        self.watching.add(key)
        if not self.watch_timer.isActive():
            self.watch_timer.start()
        self._refresh_row_for_key(key)
        self._apply_filters()

    def _stop_watching(self, key: tuple):
        self.watching.discard(key)
        if not self.watching and self.watch_timer.isActive():
            self.watch_timer.stop()
        self._refresh_row_for_key(key)
        self._apply_filters()

    def _refresh_watched(self):
        # Быстрый прицельный опрос — только тех серверов, что стоят в очереди,
        # раз в секунду, не трогая остальную таблицу.
        if self._closed or not self.watching:
            return
        for ip, port, name in self.server_defs:
            key = (ip, port)
            if key not in self.watching:
                continue
            row = self.row_for_key.get(key)
            if row is None:
                continue
            future = self.executor.submit(query_server, ip, port, name, timeout=1.5, retries=1)
            future.add_done_callback(
                lambda f, row=row, ip=ip, port=port: self._on_future_done(f, row, ip, port)
            )

    def _refresh_row_for_key(self, key: tuple):
        row = self.row_for_key.get(key)
        status = self.statuses.get(key)
        if row is not None and status is not None:
            self._update_button(row, status)

    def _update_button(self, row: int, status: ServerStatus):
        btn = self.buttons.get(row)
        if btn is None:
            return
        key = (status.ip, status.port)
        watching = key in self.watching
        full = is_server_full(status)
        unavailable = is_server_unavailable(status)

        btn.setEnabled(True)
        if watching:
            btn.setText("Отменить")
            if not status.online:
                btn.setToolTip(
                    "Сервер сейчас недоступен (перезапуск?) — подключимся "
                    "автоматически, как только он снова заработает. Нажми, "
                    "чтобы отменить ожидание."
                )
            else:
                btn.setToolTip(
                    "В очереди на этот сервер — подключимся автоматически, как "
                    "только освободится место. Нажми, чтобы отменить ожидание."
                )
            object_name = "WatchingButton"
        elif unavailable:
            btn.setText("Ждать сервер")
            btn.setToolTip(
                "Сервер сейчас недоступен. Нажми, чтобы дождаться, когда он "
                "снова заработает, и подключиться первым."
            )
            object_name = "FullButton"
        elif full:
            btn.setText("Ждать место")
            btn.setToolTip(
                "Сервер полон. Нажми, чтобы встать в очередь — подключимся "
                "автоматически, как только освободится место."
            )
            object_name = "FullButton"
        else:
            btn.setText("Подключиться")
            btn.setToolTip("")
            object_name = ""

        if btn.objectName() != object_name:
            btn.setObjectName(object_name)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _on_result(self, row: int, ip: str, port: int, status: ServerStatus):
        if self._closed:
            return
        self.statuses[(ip, port)] = status

        try:
            self.result_ready.emit(row, status)
        except RuntimeError:

            self._closed = True

    def shutdown(self):
        self._closed = True
        self.watch_timer.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)

    def _apply_row(self, row: int, status: ServerStatus):
        self.table.setItem(row, 0, QTableWidgetItem(status.display_name))
        self.table.setItem(row, 1, QTableWidgetItem(status.map_name or "—"))

        is_full = is_server_full(status)
        key = (status.ip, status.port)

        players_item = QTableWidgetItem(status.players_str)
        if is_full:
            players_item.setForeground(QColor(ACCENT))
        elif status.online:
            players_item.setForeground(QColor(BONE))
        self.table.setItem(row, 2, players_item)

        ping_str = f"{status.ping_ms} ms" if status.ping_ms else "—"
        self.table.setItem(row, 3, QTableWidgetItem(ping_str))

        state_item = QTableWidgetItem("● В сети" if status.online else "○ Не отвечает")
        state_item.setForeground(QColor(HOPE_GOLD if status.online else ASH))
        if not status.online and status.error:
            state_item.setToolTip(status.error)
        self.table.setItem(row, 4, state_item)

        if key in self.watching and status.online and not is_full:
            # Сервер снова доступен и не полон (вернулся после недоступности
            # или освободилось место) — подключаемся автоматически и снимаем
            # его с наблюдения.
            self.watching.discard(key)
            if not self.watching and self.watch_timer.isActive():
                self.watch_timer.stop()
            connect_to_server(status.ip, status.port, status.display_name)
            self.queue_connected.emit(status.display_name, f"{status.ip}:{status.port}")

        self._update_button(row, status)
        self._apply_filters()

    def _on_filter_toggled(self):
        config = load_config()
        config["server_filters"] = {
            "hide_offline": self.hide_offline_checkbox.isChecked(),
            "hide_full": self.hide_full_checkbox.isChecked(),
        }
        save_config(config)
        self._apply_filters()

    def _apply_filters(self):
        hide_offline = self.hide_offline_checkbox.isChecked()
        hide_full = self.hide_full_checkbox.isChecked()

        for row, (ip, port, _name) in enumerate(self.server_defs):
            key = (ip, port)
            status = self.statuses.get(key)
            if status is None:
                self.table.setRowHidden(row, False)
                continue

            is_full = is_server_full(status)

            hidden = False
            if hide_offline and not status.online and key not in self.watching:
                hidden = True
            if hide_full and is_full and key not in self.watching:
                hidden = True
            self.table.setRowHidden(row, hidden)


class RecentServersTab(ServerTab):

    def __init__(self, parent=None):
        super().__init__(
            server_defs=[],
            empty_hint=(
                "Пока пусто. Сервера появляются здесь автоматически, когда ты "
                "подключаешься к ним — как из лаунчера, так и напрямую из игры "
                "(если на вкладке «Консоль» включено слежение)."
            ),
            parent=parent,
        )

    def reload_from_history(self):
        if self._closed:
            return
        recent = get_recent_servers()[:10]
        self.server_defs = [
            (s["ip"], s["port"], s.get("name") or f'{s["ip"]}:{s["port"]}')
            for s in recent
        ]
        self.statuses = {
            (s["ip"], s["port"]): ServerStatus(
                ip=s["ip"], port=s["port"], fallback_name=s.get("name", "")
            )
            for s in recent
        }
        self.buttons = {}
        self._build_rows()
        super().refresh()

    def refresh(self):
        self.reload_from_history()

    def on_server_connected(self, ip: str, port: int):
        # Подключение обнаружено в консоли игры (строка "Connecting to"/
        # "Connected to" в console.log). Всегда добавляем/поднимаем сервер
        # наверх и перерисовываем список — раньше тут была проверка "уже
        # есть в истории -> ничего не делать", из-за которой повторные
        # заходы на один и тот же сервер просто не обновляли вкладку.
        add_recent_server(ip, port, f"{ip}:{port}", limit=10)
        self.reload_from_history()

    def on_launcher_connected(self, ip: str, port: int, name: str):
        # Подключение инициировано из самого лаунчера (Лобби/ENG Lobby/
        # автоочередь/вкладка друзей) — connect_to_server() уже записал его
        # в историю, тут только перерисовываем вкладку, чтобы сервер сразу
        # появился в списке.
        self.reload_from_history()


class FriendsTab(QWidget):

    COLUMNS = ["Друг", "Играет в", "Сервер", "", ""]

    friends_ready = pyqtSignal(list)
    friends_failed = pyqtSignal(str)
    friend_entered_lobby = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.executor = ThreadPoolExecutor(max_workers=1)
        self._closed = False
        self._friends_in_lobby: dict = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        top_bar = QHBoxLayout()
        self.only_shinri_checkbox = QCheckBox("Только Shinri Trial")
        self.only_shinri_checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.only_shinri_checkbox.setChecked(
            bool(load_config().get("friends_only_shinri", False))
        )
        self.only_shinri_checkbox.stateChanged.connect(self._on_filter_toggled)

        refresh_btn = QPushButton("⟳  Обновить")
        refresh_btn.setObjectName("RefreshButton")
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.clicked.connect(self.refresh)

        top_bar.addWidget(self.only_shinri_checkbox)
        top_bar.addStretch()
        top_bar.addWidget(refresh_btn)
        layout.addLayout(top_bar)

        self.status_label = QLabel("")
        self.status_label.setObjectName("HintLabel")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(40)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionsMovable(False)
        self.table.setColumnWidth(0, 160)
        for col, width in ((1, 130), (3, 110), (4, 150)):
            self.table.setColumnWidth(col, width)
        layout.addWidget(self.table, 1)

        self.friends_ready.connect(self._on_friends_ready)
        self.friends_failed.connect(self._on_friends_failed)

        self._busy = False
        self.auto_timer = QTimer(self)
        self.auto_timer.timeout.connect(self.refresh)
        self.auto_timer.start(FRIENDS_REFRESH_INTERVAL_MS)

        QTimer.singleShot(0, self.refresh)

    def _on_filter_toggled(self):
        config = load_config()
        config["friends_only_shinri"] = self.only_shinri_checkbox.isChecked()
        save_config(config)
        self.refresh()

    def refresh(self):
        if self._closed or self._busy:
            return
        config = load_config()
        api_key = config.get("steam_api_key", "").strip()
        profile = config.get("steam_profile", "").strip()
        if not api_key or not profile:
            self.status_label.setText(
                "Открой настройки (шестерёнка вверху) и укажи Steam Web API "
                "ключ и ссылку на свой профиль — тогда здесь появится список "
                "друзей, которые сейчас в игре."
            )
            self.table.setRowCount(0)
            return
        if self.table.rowCount() == 0:
            self.status_label.setText("Загружаю список друзей...")
        self._busy = True
        self.executor.submit(self._worker, api_key, profile)

    def _worker(self, api_key: str, profile: str):
        try:
            steam_id = resolve_steam_id(api_key, profile)
            friends = get_friends_in_gmod(api_key, steam_id)
        except Exception as e:
            self.friends_failed.emit(str(e))
            return

        seen_names = {}
        for friend in friends:
            if not (friend.server_ip and friend.server_port):
                continue
            address = (friend.server_ip, friend.server_port)
            if address not in seen_names:
                status = query_server(friend.server_ip, friend.server_port, "", timeout=2.0, retries=1)
                seen_names[address] = status.name if status.online else ""
            friend.server_name = seen_names[address]

        self.friends_ready.emit(friends)

    def _on_friends_failed(self, error: str):
        self._busy = False
        if self._closed:
            return
        self.table.setRowCount(0)
        self.status_label.setText(f"Не удалось получить список друзей: {error}")

    def _detect_lobby_joins(self, friends: list):
        lobby_names = {
            (ip, port): name for ip, port, name in (*LOBBIES, *ENG_LOBBIES)
        }

        current: dict = {}
        for friend in friends:
            if not (friend.server_ip and friend.server_port):
                continue
            address = (friend.server_ip, friend.server_port)
            lobby_name = lobby_names.get(address)
            if lobby_name:
                current[friend.steam_id] = lobby_name

        for steam_id, lobby_name in current.items():
            if self._friends_in_lobby.get(steam_id) != lobby_name:
                friend = next((f for f in friends if f.steam_id == steam_id), None)
                if friend is not None:
                    self.friend_entered_lobby.emit(friend.name, lobby_name)

        self._friends_in_lobby = current

    def _on_friends_ready(self, friends: list):
        self._busy = False
        if self._closed:
            return

        self._detect_lobby_joins(friends)

        if self.only_shinri_checkbox.isChecked():
            friends = [f for f in friends if "shinri" in f.game_name.lower()]

        self.table.setRowCount(len(friends))
        if not friends:
            self.status_label.setText(
                "Сейчас никто из друзей не играет. Учти: друзья, скрывшие "
                "игровой статус в настройках приватности Steam, здесь не видны."
            )
        else:
            self.status_label.setText(f"Сейчас в игре: {len(friends)}")

        for row, friend in enumerate(friends):
            self.table.setItem(row, 0, QTableWidgetItem(friend.name))
            self.table.setItem(row, 1, QTableWidgetItem(friend.game_name))

            server_text = friend.server_name or friend.server_address or "—"
            server_item = QTableWidgetItem(server_text)
            if friend.server_address:
                server_item.setToolTip(friend.server_address)
            self.table.setItem(row, 2, server_item)

            profile_btn = QPushButton("Профиль")
            profile_btn.setObjectName("RefreshButton")
            profile_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            profile_btn.setFixedHeight(28)
            profile_btn.setEnabled(bool(friend.profile_url))
            profile_btn.clicked.connect(
                lambda _, url=friend.profile_url: webbrowser.open(url)
            )
            self.table.setCellWidget(row, 3, profile_btn)

            join_btn = QPushButton("Присоединиться")
            join_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            join_btn.setFixedHeight(28)
            if friend.server_ip and friend.server_port:
                join_btn.clicked.connect(
                    lambda _, ip=friend.server_ip, port=friend.server_port,
                    name=friend.game_name: connect_to_server(ip, port, name)
                )
            else:
                join_btn.setEnabled(False)
                join_btn.setText("Нет сервера")
            self.table.setCellWidget(row, 4, join_btn)

    def shutdown(self):
        self._closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)


class ProfileTab(QWidget):

    profile_ready = pyqtSignal(object, object)
    profile_failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.executor = ThreadPoolExecutor(max_workers=1)
        self._closed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        search_row = QHBoxLayout()
        search_row.setSpacing(6)

        self.query_input = QLineEdit()
        self.query_input.setPlaceholderText(
            "Ссылка на профиль, SteamID64 или короткое имя профиля"
        )
        self.query_input.returnPressed.connect(self._on_search)

        search_btn = QPushButton("Найти")
        search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        search_btn.clicked.connect(self._on_search)

        search_row.addWidget(self.query_input, 1)
        search_row.addWidget(search_btn)
        layout.addLayout(search_row)

        self.status_label = QLabel(
            "Вставь ссылку на профиль Steam или SteamID — покажу всё, что "
            "отдаёт публичный API. Нужен Steam Web API ключ в настройках."
        )
        self.status_label.setObjectName("HintLabel")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(10)
        self.content_layout.addStretch()
        self.scroll.setWidget(self.content)
        self.scroll.setVisible(False)
        layout.addWidget(self.scroll, 1)

        self.profile_ready.connect(self._on_profile_ready)
        self.profile_failed.connect(self._on_profile_failed)

    def _on_search(self):
        if self._closed:
            return
        query = self.query_input.text().strip()
        if not query:
            return
        api_key = load_config().get("steam_api_key", "").strip()
        if not api_key:
            self.status_label.setText(
                "Сначала укажи Steam Web API ключ в настройках (шестерёнка вверху)."
            )
            return
        self.status_label.setText("Ищу профиль...")
        self.status_label.setVisible(True)
        self.scroll.setVisible(False)
        self.executor.submit(self._worker, api_key, query)

    def _worker(self, api_key: str, query: str):
        try:
            profile = lookup_profile(api_key, query)
        except Exception as e:
            self.profile_failed.emit(str(e))
            return
        avatar = fetch_avatar(profile.avatar_url)
        self.profile_ready.emit(profile, avatar)

    def _on_profile_failed(self, error: str):
        if self._closed:
            return
        self.scroll.setVisible(False)
        self.status_label.setVisible(True)
        self.status_label.setText(f"Не удалось найти профиль: {error}")

    def _clear_content(self):
        while self.content_layout.count() > 1:
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _make_value_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("ProfileValue")
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setCursor(Qt.CursorShape.IBeamCursor)
        label.setWordWrap(True)
        return label

    def _build_header(self, profile, avatar_bytes) -> QFrame:
        card = QFrame()
        card.setObjectName("ProfileCard")
        row = QHBoxLayout(card)
        row.setContentsMargins(16, 16, 16, 16)
        row.setSpacing(16)

        if avatar_bytes:
            pixmap = QPixmap()
            if pixmap.loadFromData(avatar_bytes):
                avatar_label = QLabel()
                avatar_label.setPixmap(pixmap.scaled(
                    96, 96,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
                avatar_label.setFixedSize(96, 96)
                row.addWidget(avatar_label, 0, Qt.AlignmentFlag.AlignTop)

        info = QVBoxLayout()
        info.setSpacing(4)

        name = QLabel(profile.name)
        name.setObjectName("ProfileName")
        name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info.addWidget(name)

        subtitle_parts = [profile.state]
        if profile.current_game and profile.current_game != "—":
            subtitle_parts.append(f"играет в {profile.current_game}")
        subtitle = QLabel("  •  ".join(subtitle_parts))
        subtitle.setObjectName("ProfileSubtitle")
        subtitle.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info.addWidget(subtitle)

        if profile.profile_url:
            link = QLabel(
                f'<a href="{profile.profile_url}" style="color:{ASH}; text-decoration:none;">'
                f'{profile.profile_url}</a>'
            )
            link.setObjectName("ProfileLink")
            link.setOpenExternalLinks(True)
            link.setCursor(Qt.CursorShape.PointingHandCursor)
            info.addWidget(link)

        info.addStretch()
        row.addLayout(info, 1)
        return card

    def _build_group(self, title: str, rows: list) -> QFrame:
        card = QFrame()
        card.setObjectName("ProfileCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(8)

        title_label = QLabel(title.upper())
        title_label.setObjectName("SettingsSectionLabel")
        box.addWidget(title_label)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        for i, (key, value) in enumerate(rows):
            key_label = QLabel(key)
            key_label.setObjectName("ProfileKey")
            grid.addWidget(key_label, i, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(self._make_value_label(value), i, 1)
        box.addLayout(grid)
        return card

    def _on_profile_ready(self, profile, avatar_bytes):
        if self._closed:
            return
        self._clear_content()

        index = 0
        self.content_layout.insertWidget(index, self._build_header(profile, avatar_bytes))
        index += 1
        for title, rows in profile_to_groups(profile):
            self.content_layout.insertWidget(index, self._build_group(title, rows))
            index += 1

        notes = " ".join(profile.extra_notes)
        if notes:
            self.status_label.setText(notes)
            self.status_label.setVisible(True)
        else:
            self.status_label.setVisible(False)
        self.scroll.setVisible(True)

    def shutdown(self):
        self._closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)


class DeselectableTable(QTableWidget):

    def mousePressEvent(self, event):
        try:
            point = event.position().toPoint()
        except AttributeError:
            point = event.pos()
        if self.itemAt(point) is None:
            self.reset_selection()
        super().mousePressEvent(event)

    def reset_selection(self):
        self.clearSelection()
        self.setCurrentCell(-1, -1)
        selection_model = self.selectionModel()
        if selection_model is not None:
            selection_model.clearCurrentIndex()


class PlayerDetailPanel(QFrame):

    comment_saved = pyqtSignal()
    panel_closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ProfileCard")
        self._current_id = None

        box = QVBoxLayout(self)
        box.setContentsMargins(16, 12, 16, 12)
        box.setSpacing(6)

        title_row = QHBoxLayout()
        self.name_label = QLabel("")
        self.name_label.setObjectName("ProfileName")
        self.name_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        close_btn = QPushButton("✕  Закрыть")
        close_btn.setObjectName("ClosePanelButton")
        close_btn.setFixedHeight(28)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self._on_close_clicked)
        title_row.addWidget(self.name_label, 1)
        title_row.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignTop)
        box.addLayout(title_row)

        self.meta_label = QLabel("")
        self.meta_label.setObjectName("ProfileSubtitle")
        self.meta_label.setWordWrap(True)
        self.meta_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(self.meta_label)

        history_title = QLabel("ГДЕ И КОГДА ПЕРЕСЕКАЛИСЬ")
        history_title.setObjectName("SettingsSectionLabel")
        box.addWidget(history_title)

        self.history = QPlainTextEdit()
        self.history.setReadOnly(True)
        self.history.setObjectName("ConsoleOutput")
        self.history.setFixedHeight(84)
        box.addWidget(self.history)

        self.comment_edit = QPlainTextEdit()
        self.comment_edit.setPlaceholderText("Заметка об игроке")
        self.comment_edit.setFixedHeight(58)
        box.addWidget(self.comment_edit)

        buttons_row = QHBoxLayout()
        open_btn = QPushButton("Открыть профиль")
        open_btn.setObjectName("RefreshButton")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(self._open_profile)
        save_btn = QPushButton("Сохранить заметку")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self._save_comment)
        buttons_row.addWidget(open_btn)
        buttons_row.addStretch()
        buttons_row.addWidget(save_btn)
        box.addLayout(buttons_row)

        self.setVisible(False)

    def show_player(self, steam_id64: str):
        entry = get_player(steam_id64)
        if entry is None:
            self.close_panel()
            return
        self._current_id = steam_id64

        self.name_label.setText(player_display_name(entry))
        other_names = ", ".join(entry.get("names", [])[1:])
        meta = [entry.get("steam_id", ""), steam_id64]
        if other_names:
            meta.append(f"другие ники: {other_names}")
        meta.append(f"встреч: {entry.get('encounters', 0)}")
        self.meta_label.setText("  •  ".join(p for p in meta if p))

        lines = [
            f"{format_stamp(s.get('date', ''))}  —  {s.get('server', '')}"
            for s in entry.get("sessions", [])[:14]
        ]
        self.history.setPlainText("\n".join(lines) or "нет данных")
        self.comment_edit.setPlainText(entry.get("comment", ""))
        self.setVisible(True)

    def _on_close_clicked(self):
        self.close_panel()
        self.panel_closed.emit()

    def close_panel(self):
        self._current_id = None
        self.setVisible(False)

    def _save_comment(self):
        if not self._current_id:
            return
        set_comment(self._current_id, self.comment_edit.toPlainText().strip())
        self.comment_saved.emit()

    def _open_profile(self):
        if self._current_id:
            webbrowser.open(f"https://steamcommunity.com/profiles/{self._current_id}")


def _parse_duration_seconds(value: str) -> int:
    seconds = 0
    for part in value.split(":"):
        try:
            seconds = seconds * 60 + int(part)
        except ValueError:
            return 0
    return seconds


class StatusTab(QWidget):

    avatar_loaded = pyqtSignal(str, object)
    summaries_loaded = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._closed = False
        self._snapshot = None
        self._avatar_cache: dict = {}
        self._row_by_id: dict = {}
        self._sorted_players: list = []
        self.executor = ThreadPoolExecutor(max_workers=6)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        self.header_card = QFrame()
        self.header_card.setObjectName("ProfileCard")
        header_layout = QVBoxLayout(self.header_card)
        header_layout.setContentsMargins(16, 12, 16, 12)
        header_layout.setSpacing(4)

        self.server_label = QLabel("Нет данных")
        self.server_label.setObjectName("ProfileName")
        self.server_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        header_layout.addWidget(self.server_label)

        self.meta_label = QLabel(
            "Напиши в игровой консоли команду status — лаунчер сам разберёт "
            "вывод и покажет здесь список игроков."
        )
        self.meta_label.setObjectName("ProfileSubtitle")
        self.meta_label.setWordWrap(True)
        self.meta_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        header_layout.addWidget(self.meta_label)

        self.updated_label = QLabel("")
        self.updated_label.setObjectName("FileSizeLabel")
        header_layout.addWidget(self.updated_label)

        self.notes_label = QLabel("")
        self.notes_label.setObjectName("ProfileSubtitle")
        header_layout.addWidget(self.notes_label)

        layout.addWidget(self.header_card)

        sort_row = QHBoxLayout()
        sort_row.addWidget(QLabel("Сортировка:"))
        self.sort_combo = QComboBox()
        self.sort_combo.addItems([
            "Без сортировки",
            "По нику (А-Я)",
            "По времени в игре (сначала долгие)",
            "По заметкам (сначала с заметками)",
        ])
        self.sort_combo.currentIndexChanged.connect(self._render_rows)
        sort_row.addWidget(self.sort_combo)
        sort_row.addStretch()
        layout.addLayout(sort_row)

        self.table = DeselectableTable(0, 7)
        self.table.setHorizontalHeaderLabels(["", "Ник", "📝", "SteamID", "В игре", "Пинг", ""])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionsMovable(False)
        for col, width in ((0, 44), (2, 36), (3, 170), (4, 90), (5, 70), (6, 120)):
            self.table.setColumnWidth(col, width)
        self.table.setAutoScroll(False)
        self.table.setIconSize(QSize(32, 32))
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.cellClicked.connect(self._on_row_clicked)
        layout.addWidget(self.table, 1)

        self.detail_panel = PlayerDetailPanel()
        self.detail_panel.comment_saved.connect(self._on_comment_saved)
        self.detail_panel.panel_closed.connect(self.table.reset_selection)
        layout.addWidget(self.detail_panel)

        self.avatar_loaded.connect(self._on_avatar_loaded)
        self.summaries_loaded.connect(self._on_summaries_loaded)

    def apply_snapshot(self, snapshot):
        if self._closed:
            return
        self._snapshot = snapshot
        self._row_by_id = {}

        self.server_label.setText(snapshot.hostname or "Сервер")
        meta_parts = []
        if snapshot.map_name:
            meta_parts.append(f"карта {snapshot.map_name}")
        if snapshot.address:
            meta_parts.append(snapshot.address)
        if snapshot.players_line:
            meta_parts.append(snapshot.players_line)
        if snapshot.uptime:
            meta_parts.append(f"аптайм {snapshot.uptime}")
        self.meta_label.setText("  •  ".join(meta_parts) if meta_parts else "—")
        self.updated_label.setText(
            f"обновлено: {snapshot.captured_at.strftime('%d.%m.%Y %H:%M:%S')}"
        )

        new_players = record_encounter(snapshot.players, snapshot.hostname, snapshot.map_name)

        if new_players:
            self.updated_label.setText(
                self.updated_label.text() + f"   •   новых игроков в базе: {new_players}"
            )

        self._render_rows()

        api_key = load_config().get("steam_api_key", "").strip()
        if api_key:
            ids = [p.steam_id64 for p in snapshot.players if p.steam_id64]
            self.executor.submit(self._load_summaries, api_key, ids)

    def _render_rows(self):
        if self._snapshot is None:
            return
        snapshot = self._snapshot
        comments = get_comments_map()

        players = list(snapshot.players)
        sort_mode = self.sort_combo.currentIndex()
        if sort_mode == 1:
            players.sort(key=lambda p: p.name.lower())
        elif sort_mode == 2:
            players.sort(key=lambda p: _parse_duration_seconds(p.connected), reverse=True)
        elif sort_mode == 3:
            players.sort(key=lambda p: bool(comments.get(p.steam_id64)), reverse=True)

        self._sorted_players = players
        self._row_by_id = {}

        self.table.setRowCount(len(players))
        for row, player in enumerate(players):
            self._row_by_id[player.steam_id64] = row

            avatar_item = QTableWidgetItem()
            self.table.setItem(row, 0, avatar_item)

            name_item = QTableWidgetItem(player.name)
            note = comments.get(player.steam_id64)
            if note:
                name_item.setForeground(QColor(HOPE_GOLD))
                name_item.setToolTip(note)
            self.table.setItem(row, 1, name_item)

            note_item = QTableWidgetItem("📝" if note else "")
            note_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if note:
                note_item.setToolTip(note)
            self.table.setItem(row, 2, note_item)

            self.table.setItem(row, 3, QTableWidgetItem(player.steam_id))
            self.table.setItem(row, 4, QTableWidgetItem(player.connected))

            ping_item = QTableWidgetItem(str(player.ping))
            if player.ping >= 200:
                ping_item.setForeground(QColor(ACCENT))
            self.table.setItem(row, 5, ping_item)

            profile_btn = QPushButton("Профиль")
            profile_btn.setObjectName("RefreshButton")
            profile_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            profile_btn.setFixedHeight(28)
            url = f"https://steamcommunity.com/profiles/{player.steam_id64}"
            profile_btn.clicked.connect(lambda _, u=url: webbrowser.open(u))
            self.table.setCellWidget(row, 6, profile_btn)

        noted_here = sum(1 for p in snapshot.players if comments.get(p.steam_id64))
        if noted_here:
            self.notes_label.setText(
                f"📝  игроков с заметками на сервере: {noted_here} — нажми на строку, "
                "чтобы прочитать или изменить"
            )
        else:
            self.notes_label.setText(
                "Заметок ни у кого из присутствующих нет — нажми на строку, чтобы добавить"
            )

    def _on_row_clicked(self, row: int, _column: int):
        if 0 <= row < len(self._sorted_players):
            self.detail_panel.show_player(self._sorted_players[row].steam_id64)

    def _on_comment_saved(self):
        if self._snapshot is not None:
            self.apply_snapshot(self._snapshot)

    def _load_summaries(self, api_key: str, ids: list):
        try:
            summaries = fetch_summaries_map(api_key, ids)
        except Exception:
            return
        self.summaries_loaded.emit(summaries)

    def _on_summaries_loaded(self, summaries: dict):
        if self._closed:
            return
        for steam_id, info in summaries.items():
            url = info.get("avatar", "")
            if not url:
                continue
            if url in self._avatar_cache:
                self._apply_avatar(steam_id, self._avatar_cache[url])
            else:
                self.executor.submit(self._load_avatar, steam_id, url)

    def _load_avatar(self, steam_id: str, url: str):
        data = fetch_avatar(url)
        if data:
            self._avatar_cache[url] = data
            self.avatar_loaded.emit(steam_id, data)

    def _on_avatar_loaded(self, steam_id: str, data):
        if self._closed:
            return
        self._apply_avatar(steam_id, data)

    def _apply_avatar(self, steam_id: str, data):
        row = self._row_by_id.get(steam_id)
        if row is None:
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return
        item = self.table.item(row, 0)
        if item is None:
            return
        item.setIcon(QIcon(pixmap.scaled(
            32, 32,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )))

    def shutdown(self):
        self._closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)


class PlayersTab(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._closed = False
        self._current_id = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        top_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Поиск по нику, SteamID или заметке")
        self.search_input.textChanged.connect(self._apply_filter)

        self.only_noted_checkbox = QCheckBox("Только с заметками")
        self.only_noted_checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.only_noted_checkbox.stateChanged.connect(self._apply_filter)

        self.sort_combo = QComboBox()
        self.sort_combo.addItems([
            "Сначала недавние",
            "По нику (А-Я)",
            "По встречам (сначала больше)",
            "По заметкам (сначала с заметками)",
        ])
        self.sort_combo.currentIndexChanged.connect(self._apply_filter)

        refresh_btn = QPushButton("⟳  Обновить")
        refresh_btn.setObjectName("RefreshButton")
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.clicked.connect(self.reload)

        fix_btn = QPushButton("Пересчитать встречи")
        fix_btn.setObjectName("RefreshButton")
        fix_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fix_btn.setToolTip(
            "Исправляет накрученные счётчики: число встреч станет равным "
            "количеству записанных сессий"
        )
        fix_btn.clicked.connect(self._fix_counts)

        clear_btn = QPushButton("Очистить базу")
        clear_btn.setObjectName("RefreshButton")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._clear_database)

        top_row.addWidget(self.search_input, 1)
        top_row.addWidget(self.only_noted_checkbox)
        top_row.addWidget(self.sort_combo)
        top_row.addWidget(fix_btn)
        top_row.addWidget(clear_btn)
        top_row.addWidget(refresh_btn)
        layout.addLayout(top_row)

        self.count_label = QLabel("")
        self.count_label.setObjectName("HintLabel")
        layout.addWidget(self.count_label)

        self.table = DeselectableTable(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Ник", "Встреч", "Первая встреча", "Последняя встреча", "Заметка"]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionsMovable(False)
        for col, width in ((1, 70), (2, 140), (3, 140), (4, 200)):
            self.table.setColumnWidth(col, width)
        self.table.setAutoScroll(False)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.cellClicked.connect(self._on_row_clicked)
        layout.addWidget(self.table, 1)

        self.detail_panel = PlayerDetailPanel()
        self.detail_panel.comment_saved.connect(self.reload)
        self.detail_panel.panel_closed.connect(self.table.reset_selection)
        layout.addWidget(self.detail_panel)

        self._all_players: list = []
        QTimer.singleShot(0, self.reload)

    def reload(self):
        if self._closed:
            return
        self._all_players = get_all_players()
        self._apply_filter()

    def _apply_filter(self):
        query = self.search_input.text().strip().lower()
        only_noted = self.only_noted_checkbox.isChecked()

        rows = []
        for entry in self._all_players:
            if only_noted and not entry.get("comment"):
                continue
            if query:
                haystack = " ".join(entry.get("names", [])).lower()
                haystack += " " + entry.get("steam_id64", "")
                haystack += " " + entry.get("steam_id", "").lower()
                haystack += " " + entry.get("comment", "").lower()
                if query not in haystack:
                    continue
            rows.append(entry)

        sort_mode = self.sort_combo.currentIndex()
        if sort_mode == 1:
            rows.sort(key=lambda e: player_display_name(e).lower())
        elif sort_mode == 2:
            rows.sort(key=lambda e: e.get("encounters", 0), reverse=True)
        elif sort_mode == 3:
            rows.sort(key=lambda e: bool(e.get("comment")), reverse=True)

        self._visible = rows
        self.table.setRowCount(len(rows))
        for row, entry in enumerate(rows):
            name_item = QTableWidgetItem(player_display_name(entry))
            if entry.get("comment"):
                name_item.setForeground(QColor(HOPE_GOLD))
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(str(entry.get("encounters", 0))))
            self.table.setItem(row, 2, QTableWidgetItem(format_stamp(entry.get("first_seen", ""))))
            self.table.setItem(row, 3, QTableWidgetItem(format_stamp(entry.get("last_seen", ""))))
            comment = entry.get("comment", "").replace("\n", " ")
            self.table.setItem(row, 4, QTableWidgetItem(comment[:60]))

        total = len(self._all_players)
        noted = sum(1 for e in self._all_players if e.get("comment"))
        self.count_label.setText(
            f"Всего игроков в базе: {total}   •   с заметками: {noted}   •   показано: {len(rows)}"
        )

    def _fix_counts(self):
        count = reset_encounter_counts()
        self.reload()
        self.count_label.setText(
            f"Счётчики пересчитаны по истории сессий у {count} игроков."
        )

    def _clear_database(self):
        answer = QMessageBox.question(
            self,
            "Очистить базу игроков",
            "Удалить всех игроков вместе с историей встреч и заметками?\n"
            "Это действие нельзя отменить.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        clear_all()
        self.detail_panel.close_panel()
        self.reload()

    def _on_row_clicked(self, row: int, _column: int):
        if 0 <= row < len(self._visible):
            self.detail_panel.show_player(self._visible[row].get("steam_id64", ""))

    def shutdown(self):
        self._closed = True


class ConsoleTab(QWidget):

    line_received = pyqtSignal(str)
    watch_error = pyqtSignal(str)
    server_connected = pyqtSignal(str, int)
    status_ready = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.watcher: Optional[ConsoleLogWatcher] = None
        self._closed = False
        self.entries: list = []
        self.status_collector = StatusCollector()
        self._status_flush_timer = QTimer(self)
        self._status_flush_timer.setSingleShot(True)
        self._status_flush_timer.timeout.connect(self._flush_status)
        self._auto_retry_enabled = True
        self._last_connect_key: Optional[tuple] = None
        self._last_connect_time: float = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        path_row = QHBoxLayout()
        path_row.setSpacing(6)

        saved_path = load_config().get("console_log_path", "")
        if not saved_path:
            auto = find_console_log()
            saved_path = str(auto) if auto else ""

        self.path_input = QLineEdit(saved_path)
        self.path_input.setPlaceholderText(
            r"Путь к console.log, например C:\...\GarrysMod\garrysmod\console.log"
        )

        browse_btn = QPushButton("Обзор")
        browse_btn.setObjectName("RefreshButton")
        browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_btn.clicked.connect(self._on_browse_clicked)

        self.watch_btn = QPushButton("Начать слежение")
        self.watch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.watch_btn.clicked.connect(self._on_watch_clicked)

        path_row.addWidget(QLabel("Файл:"))
        path_row.addWidget(self.path_input, 1)
        path_row.addWidget(browse_btn)
        path_row.addWidget(self.watch_btn)
        layout.addLayout(path_row)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)

        self.status_label = QLabel(
            "Добавь -condebug в параметры запуска GMod (Steam → Свойства → Общие), "
            "тогда игра будет писать всю консоль в console.log, а лаунчер покажет "
            "её здесь в реальном времени."
        )
        self.status_label.setObjectName("HintLabel")
        self.status_label.setWordWrap(True)

        self.size_label = QLabel("")
        self.size_label.setObjectName("FileSizeLabel")

        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.size_label, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(status_row)

        self.size_timer = QTimer(self)
        self.size_timer.timeout.connect(self._update_file_size)
        self.size_timer.start(3000)
        self._update_file_size()

        self.autostart_timer = QTimer(self)
        self.autostart_timer.timeout.connect(self._retry_autostart)
        self.autostart_timer.start(4000)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Поиск по консоли (со временем)")
        self.search_input.textChanged.connect(self._rebuild_output)
        layout.addWidget(self.search_input)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setObjectName("ConsoleOutput")
        self.output.setMaximumBlockCount(5000)
        layout.addWidget(self.output, 1)

        bottom_row = QHBoxLayout()
        self.autoscroll_checkbox = QCheckBox("Автопрокрутка вниз")
        self.autoscroll_checkbox.setChecked(True)
        self.autoscroll_checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn = QPushButton("Очистить")
        clear_btn.setObjectName("RefreshButton")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._clear_output)
        bottom_row.addWidget(self.autoscroll_checkbox)
        bottom_row.addStretch()
        bottom_row.addWidget(clear_btn)
        layout.addLayout(bottom_row)

        self.line_received.connect(self._append_line)
        self.watch_error.connect(self._on_watch_error)

        QTimer.singleShot(0, self._try_autostart)

    def _try_autostart(self):
        if self._closed or not self.path_input.text().strip():
            return
        if not is_gmod_running():
            self.status_label.setText(
                "Garry's Mod не запущен — слежение не начато. Запусти игру "
                "и нажми «Начать слежение» (или просто подожди — лаунчер "
                "сам начнёт следить, как только игра откроется)."
            )
            return
        self._start_watching(silent=True)

    def _retry_autostart(self):
        # Периодическая проверка: если слежение ещё не идёт (например, при
        # старте лаунчера GMod ещё не был запущен) и пользователь сам его
        # не останавливал — включаем, как только игра появится в процессах.
        if self._closed or not self._auto_retry_enabled:
            return
        if self.watcher is not None and self.watcher.is_running():
            return
        if not self.path_input.text().strip():
            return
        if is_gmod_running():
            self._start_watching(silent=True)

    def _on_browse_clicked(self):
        gmod_dirs = find_gmod_dirs()
        start_dir = str(gmod_dirs[0]) if gmod_dirs else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Выбери console.log", start_dir, "Лог (*.log);;Все файлы (*)"
        )
        if path:
            self.path_input.setText(path)

    def _on_watch_clicked(self):
        if self.watcher is not None and self.watcher.is_running():
            self._stop_watching()
            return
        self._start_watching()

    def _start_watching(self, silent: bool = False):
        path_str = self.path_input.text().strip()
        if not path_str:
            if not silent:
                self.status_label.setText("Укажи путь к файлу console.log.")
            return

        config = load_config()
        config["console_log_path"] = path_str
        save_config(config)

        watcher = ConsoleLogWatcher(Path(path_str))
        watcher.on_line = lambda line: self.line_received.emit(line)
        watcher.on_error = lambda err: self.watch_error.emit(err)
        try:
            watcher.start()
        except Exception as e:
            if not silent:
                self.status_label.setText(f"Не удалось начать слежение: {e}")
            return

        self.watcher = watcher
        self._auto_retry_enabled = True
        self.watch_btn.setText("Остановить")
        self.status_label.setText(f"Слежение за файлом: {path_str}")
        self.output.appendPlainText("--- Слежение за консолью запущено ---")

    def _stop_watching(self):
        if self.watcher is not None:
            self.watcher.stop()
            self.watcher = None
        self._auto_retry_enabled = False
        self.watch_btn.setText("Начать слежение")
        self.status_label.setText("Слежение остановлено.")
        self.output.appendPlainText("--- Слежение остановлено ---")

    def _update_file_size(self):
        if self._closed:
            return
        path_str = self.path_input.text().strip()
        if not path_str:
            self.size_label.setText("")
            return
        try:
            size = Path(path_str).stat().st_size
        except Exception:
            self.size_label.setText("")
            return
        self.size_label.setText(f"размер лога: {_format_size(size)}")

    def _append_line(self, line: str):
        if self._closed:
            return

        stamp = datetime.now().strftime("%H:%M:%S")
        self.entries.append((stamp, line))
        if len(self.entries) > MAX_CONSOLE_ENTRIES:
            del self.entries[:len(self.entries) - MAX_CONSOLE_ENTRIES]

        query = self.search_input.text().strip().lower()
        if query:
            if query in line.lower():
                self.output.appendPlainText(f"[{stamp}]  {line}")
            else:
                self._detect_connection(line)
                return
        else:
            self.output.appendPlainText(line)

        if self.autoscroll_checkbox.isChecked():
            scrollbar = self.output.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
        self._detect_connection(line)

    def _rebuild_output(self):
        query = self.search_input.text().strip().lower()
        self.output.clear()
        if query:
            matches = [(t, l) for t, l in self.entries if query in l.lower()]
            if not matches:
                self.output.appendPlainText("--- Ничего не найдено ---")
            for stamp, line in matches:
                self.output.appendPlainText(f"[{stamp}]  {line}")
        else:
            for _stamp, line in self.entries:
                self.output.appendPlainText(line)
        scrollbar = self.output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _clear_output(self):
        self.entries.clear()
        self.output.clear()

    def _detect_connection(self, line: str):
        snapshot = self.status_collector.feed(line)
        if snapshot:
            self.status_ready.emit(snapshot)
        elif self.status_collector.has_pending_players():
            self._status_flush_timer.start(1200)

        match = CONNECTED_RE.search(line)
        if not match:
            return
        ip = match.group(1)
        try:
            port = int(match.group(2))
        except ValueError:
            return

        # Одно подключение обычно даёт в логе и "Connecting to", и
        # "Connected to" — обе строки матчатся регуляркой. Не шлём сигнал
        # второй раз подряд для того же адреса, чтобы не дёргать вкладку
        # «Последние сервера» дважды на одно и то же подключение.
        key = (ip, port)
        now = time.monotonic()
        if key == self._last_connect_key and now - self._last_connect_time < 15:
            return
        self._last_connect_key = key
        self._last_connect_time = now
        self.server_connected.emit(ip, port)

    def _flush_status(self):
        snapshot = self.status_collector.flush()
        if snapshot:
            self.status_ready.emit(snapshot)

    def _on_watch_error(self, error: str):
        if self._closed:
            return
        self.status_label.setText(f"Ошибка чтения файла: {error}")

    def shutdown(self):
        self._closed = True
        self.autostart_timer.stop()
        if self.watcher is not None:
            self.watcher.stop()
            self.watcher = None


class UpdateDialog(QDialog):

    def __init__(self, update_info: dict, parent=None):
        super().__init__(parent)
        self.update_info = update_info
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setObjectName("SettingsDialog")
        self.setFixedSize(460, 260)
        if parent is not None:
            geo = parent.geometry()
            self.move(
                geo.x() + (geo.width() - self.width()) // 2,
                geo.y() + (geo.height() - self.height()) // 2,
            )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setObjectName("SettingsHeader")
        header.setFixedHeight(44)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 0, 0, 0)
        title = QLabel("ДОСТУПНО ОБНОВЛЕНИЕ")
        title.setObjectName("TitleBarText")
        header_layout.addWidget(title)
        header_layout.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setObjectName("TitleBarCloseButton")
        close_btn.setFixedSize(44, 44)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        header_layout.addWidget(close_btn)
        outer.addWidget(header)

        body = QWidget()
        body.setObjectName("SettingsBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(28, 24, 28, 24)
        body_layout.setSpacing(10)

        version_label = QLabel(f"Новая версия: {update_info['version']}")
        version_label.setObjectName("SettingsSectionLabel")
        body_layout.addWidget(version_label)

        notes = update_info.get("notes", "").strip()
        if notes:
            notes_label = QLabel(notes[:300])
            notes_label.setObjectName("HintLabel")
            notes_label.setWordWrap(True)
            body_layout.addWidget(notes_label)

        body_layout.addStretch()

        buttons_row = QHBoxLayout()
        later_btn = QPushButton("Позже")
        later_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        later_btn.clicked.connect(self.close)
        download_btn = QPushButton("Скачать")
        download_btn.setObjectName("MenuButtonPrimary")
        download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        download_btn.clicked.connect(self._on_download_clicked)
        buttons_row.addWidget(later_btn)
        buttons_row.addWidget(download_btn)
        body_layout.addLayout(buttons_row)

        outer.addWidget(body, 1)

    def _on_download_clicked(self):
        webbrowser.open(self.update_info["url"])
        self.close()


class NotificationsPopup(QDialog):

    def __init__(self, notifications, anchor_widget, parent_window):
        super().__init__(parent_window)
        self.setWindowFlags(
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        )
        self.setObjectName("NotificationsPopup")
        self.setFixedWidth(420)

        # Отступ 2px по краю — чтобы QScrollArea не перекрывал рамку QDialog.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(0)

        header = QFrame()
        header.setObjectName("NotificationsHeader")
        header.setFixedHeight(38)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(14, 0, 0, 0)
        title = QLabel("УВЕДОМЛЕНИЯ")
        title.setObjectName("TitleBarText")
        hl.addWidget(title)
        outer.addWidget(header)

        scroll = QScrollArea()
        scroll.setObjectName("NotificationsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        body = QWidget()
        body.setObjectName("NotificationsBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(12, 12, 12, 12)
        body_layout.setSpacing(10)

        if notifications:
            for item in notifications:
                body_layout.addWidget(self._build_item(item))
        else:
            empty = QLabel("Уведомлений пока нет.")
            empty.setObjectName("HintLabel")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            body_layout.addWidget(empty)

        body_layout.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self._fit_height(len(notifications))
        self._position(anchor_widget)

    def _build_item(self, item: dict) -> QFrame:
        frame = QFrame()
        frame.setObjectName("NotificationItem")
        box = QVBoxLayout(frame)
        box.setContentsMargins(12, 10, 12, 10)
        box.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(8)
        title_lbl = QLabel(item["title"])
        title_lbl.setObjectName("NotificationTitle")
        title_lbl.setWordWrap(True)
        top.addWidget(title_lbl, 1)
        if item.get("date"):
            date_lbl = QLabel(item["date"])
            date_lbl.setObjectName("NotificationDate")
            top.addWidget(date_lbl, 0, Qt.AlignmentFlag.AlignTop)
        box.addLayout(top)

        if item.get("body"):
            body_lbl = QLabel(item["body"])
            body_lbl.setObjectName("NotificationBody")
            body_lbl.setWordWrap(True)
            body_lbl.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            box.addWidget(body_lbl)

        return frame

    def _fit_height(self, count: int):
        if count == 0:
            self.setFixedHeight(220)
            return
        estimate = 60 + count * 125
        self.setFixedHeight(max(340, min(estimate, 680)))

    def _position(self, anchor):
        btn_top = anchor.mapToGlobal(QPoint(0, 0))
        btn_bottom = anchor.mapToGlobal(QPoint(0, anchor.height()))
        x = btn_bottom.x() + anchor.width() - self.width()
        y = btn_bottom.y() + 4

        screen = anchor.screen() or QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            if y + self.height() > geo.bottom():
                y = btn_top.y() - self.height() - 4
            if x < geo.left():
                x = geo.left() + 4
            if x + self.width() > geo.right():
                x = geo.right() - self.width() - 4

        self.move(x, y)


class SettingsDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setObjectName("SettingsDialog")
        self.setFixedSize(620, 600)
        self.recenter_on_parent()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 10)
        outer.setSpacing(0)

        header = QFrame()
        header.setObjectName("SettingsHeader")
        header.setFixedHeight(44)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 0, 0, 0)
        title = QLabel("НАСТРОЙКИ")
        title.setObjectName("TitleBarText")
        header_layout.addWidget(title)
        header_layout.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setObjectName("TitleBarCloseButton")
        close_btn.setFixedSize(44, 44)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        header_layout.addWidget(close_btn)
        outer.addWidget(header)

        scroll = QScrollArea()
        scroll.setObjectName("SettingsScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        body = QWidget()
        body.setObjectName("SettingsBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(28, 24, 34, 24)
        body_layout.setSpacing(10)


        tray_section = QLabel("СИСТЕМНЫЙ ТРЕЙ")
        tray_section.setObjectName("SettingsSectionLabel")
        body_layout.addWidget(tray_section)

        self.tray_checkbox = QCheckBox("Сворачивать в трей при закрытии окна")
        self.tray_checkbox.setChecked(bool(load_config().get("close_to_tray", False)))
        self.tray_checkbox.stateChanged.connect(self._on_tray_setting_changed)
        body_layout.addWidget(self.tray_checkbox)

        tray_hint = QLabel(
            "Если включено, кнопка ✕ сворачивает лаунчер в трей вместо "
            "закрытия — он продолжит работать в фоне (список серверов "
            "по-прежнему будет обновляться). Открыть окно обратно можно "
            "через иконку в трее."
        )
        tray_hint.setObjectName("HintLabel")
        tray_hint.setWordWrap(True)
        body_layout.addWidget(tray_hint)

        body_layout.addSpacing(16)

        notify_section = QLabel("УВЕДОМЛЕНИЯ")
        notify_section.setObjectName("SettingsSectionLabel")
        body_layout.addWidget(notify_section)

        self.friend_notify_checkbox = QCheckBox("Уведомлять, когда друг заходит в лобби")
        self.friend_notify_checkbox.setChecked(
            bool(load_config().get("friend_lobby_notifications", True))
        )
        self.friend_notify_checkbox.stateChanged.connect(self._on_friend_notify_changed)
        body_layout.addWidget(self.friend_notify_checkbox)

        notify_hint = QLabel(
            "Показывать всплывающее уведомление Windows, когда кто-то из "
            "друзей заходит в одно из лобби из списка."
        )
        notify_hint.setObjectName("HintLabel")
        notify_hint.setWordWrap(True)
        body_layout.addWidget(notify_hint)

        body_layout.addSpacing(16)

        steam_section = QLabel("STEAM (СПИСОК ДРУЗЕЙ)")
        steam_section.setObjectName("SettingsSectionLabel")
        body_layout.addWidget(steam_section)

        config = load_config()

        key_row = QHBoxLayout()
        key_row.setSpacing(6)

        self.api_key_input = QLineEdit(config.get("steam_api_key", ""))
        self.api_key_input.setPlaceholderText("Steam Web API ключ")
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.editingFinished.connect(self._on_steam_fields_changed)

        self.show_key_btn = QPushButton("Показать")
        self.show_key_btn.setObjectName("RefreshButton")
        self.show_key_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.show_key_btn.setCheckable(True)
        self.show_key_btn.toggled.connect(self._on_toggle_key_visibility)

        key_row.addWidget(self.api_key_input, 1)
        key_row.addWidget(self.show_key_btn)
        body_layout.addLayout(key_row)

        self.profile_input = QLineEdit(config.get("steam_profile", ""))
        self.profile_input.setPlaceholderText(
            "Ссылка на твой профиль или SteamID64"
        )
        self.profile_input.editingFinished.connect(self._on_steam_fields_changed)
        body_layout.addWidget(self.profile_input)

        key_btn = QPushButton("Получить ключ")
        key_btn.setObjectName("RefreshButton")
        key_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        key_btn.clicked.connect(lambda: webbrowser.open(API_KEY_PAGE))
        body_layout.addWidget(key_btn, 0, Qt.AlignmentFlag.AlignLeft)

        steam_hint = QLabel(
            "Ключ бесплатный: нажми кнопку выше, войди своим аккаунтом, в поле "
            "домена укажи любое значение (например localhost) и скопируй ключ "
            "сюда. Друзья, скрывшие игровой статус в настройках приватности, "
            "видны не будут."
        )
        steam_hint.setObjectName("HintLabel")
        steam_hint.setWordWrap(True)
        body_layout.addWidget(steam_hint)

        body_layout.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

    def _on_toggle_key_visibility(self, checked: bool):
        if checked:
            self.api_key_input.setEchoMode(QLineEdit.EchoMode.Normal)
            self.show_key_btn.setText("Скрыть")
        else:
            self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.show_key_btn.setText("Показать")

    def _on_friend_notify_changed(self, state):
        config = load_config()
        config["friend_lobby_notifications"] = bool(state)
        save_config(config)

    def _on_steam_fields_changed(self):
        config = load_config()
        config["steam_api_key"] = self.api_key_input.text().strip()
        config["steam_profile"] = self.profile_input.text().strip()
        save_config(config)

    def closeEvent(self, event):
        if hasattr(self, "show_key_btn") and self.show_key_btn.isChecked():
            self.show_key_btn.setChecked(False)
        super().closeEvent(event)

    def recenter_on_parent(self):
        parent = self.parent()
        if parent is not None:
            geo = parent.geometry()
            self.move(
                geo.x() + (geo.width() - self.width()) // 2,
                geo.y() + (geo.height() - self.height()) // 2,
            )

    def _on_tray_setting_changed(self, state):
        config = load_config()
        config["close_to_tray"] = bool(state)
        save_config(config)


class TitleBar(QFrame):

    def __init__(self, target_window: QMainWindow, parent=None):
        super().__init__(parent)
        self.target_window = target_window
        self.setObjectName("TitleBar")
        self.setFixedHeight(40)
        self._drag_offset = None
        self._settings_dialog = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 0, 0)
        layout.setSpacing(8)

        if ICON_PNG.exists():
            icon_label = QLabel()
            pixmap = QPixmap(str(ICON_PNG)).scaled(
                20, 20, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            icon_label.setPixmap(pixmap)
            layout.addWidget(icon_label)

        name_label = QLabel("SHINRI TRIAL LAUNCHER")
        name_label.setObjectName("TitleBarText")
        layout.addWidget(name_label)

        layout.addStretch()

        self.notifications_btn = QPushButton()
        self.notifications_btn.setObjectName("TitleBarButton")
        self.notifications_btn.setFixedSize(46, 40)
        self.notifications_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.notifications_btn.setToolTip("Уведомления")
        self.notifications_btn.setIcon(_make_bell_icon(ASH))
        self.notifications_btn.setIconSize(QSize(20, 20))
        self.notifications_btn.clicked.connect(self._on_notifications_clicked)

        settings_btn = QPushButton("⚙")
        settings_btn.setObjectName("TitleBarButton")
        settings_btn.setFixedSize(46, 40)
        settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_btn.clicked.connect(self._open_settings)

        min_btn = QPushButton("—")
        min_btn.setObjectName("TitleBarButton")
        min_btn.setFixedSize(46, 40)
        min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        min_btn.clicked.connect(self.target_window.showMinimized)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("TitleBarCloseButton")
        close_btn.setFixedSize(46, 40)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.target_window.close)

        layout.addWidget(self.notifications_btn)
        layout.addWidget(settings_btn)
        layout.addWidget(min_btn)
        layout.addWidget(close_btn)

    def _on_notifications_clicked(self):
        self.target_window.show_notifications_popup(self.notifications_btn)

    def set_notifications_state(self, unread: bool):
        name = "TitleBarButtonNotified" if unread else "TitleBarButton"
        if self.notifications_btn.objectName() != name:
            self.notifications_btn.setObjectName(name)
            self.notifications_btn.style().unpolish(self.notifications_btn)
            self.notifications_btn.style().polish(self.notifications_btn)
        color = HOPE_GOLD if unread else ASH
        self.notifications_btn.setIcon(_make_bell_icon(color))
        self.notifications_btn.setToolTip(
            "Уведомления (есть новые)" if unread else "Уведомления"
        )

    def _open_settings(self):
        if self._settings_dialog is None:
            self._settings_dialog = SettingsDialog(self.target_window)
        else:
            self._settings_dialog.recenter_on_parent()
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()


    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.target_window.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.target_window.move(event.globalPosition().toPoint() - self._drag_offset)
            if self._settings_dialog is not None and self._settings_dialog.isVisible():
                self._settings_dialog.recenter_on_parent()
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_offset = None


class LauncherWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        if ICON_ICO.exists():
            self.setWindowIcon(QIcon(str(ICON_ICO)))
        self.setWindowTitle("Shinri Trial Launcher")
        self.setFixedSize(1104, 720)

        central = QWidget()
        central.setObjectName("RootContainer")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.title_bar = TitleBar(self)
        root.addWidget(self.title_bar)
        root.addSpacing(4)

        tabs = QTabWidget()

        self.lobbies_tab = ServerTab(
            LOBBIES,
            empty_hint=(
                "Список лобби пуст. Впиши реальные сервера в servers.py "
                "(список LOBBIES) — формат: (ip, port, название)."
            ),
        )
        self.lobbies_tab.queue_connected.connect(self._on_queue_connected)
        tabs.addTab(self.lobbies_tab, "Лобби")

        self.eng_lobbies_tab = ServerTab(
            ENG_LOBBIES,
            empty_hint=(
                "Список ENG лобби пуст. Впиши реальные сервера в servers.py "
                "(список ENG_LOBBIES) — формат: (ip, port, название)."
            ),
        )
        self.eng_lobbies_tab.queue_connected.connect(self._on_queue_connected)
        tabs.addTab(self.eng_lobbies_tab, "ENG Lobby")

        self.recent_tab = RecentServersTab()
        connection_events.connected.connect(self.recent_tab.on_launcher_connected)
        tabs.addTab(self.recent_tab, "Последние сервера")

        self.friends_tab = FriendsTab()
        self.friends_tab.friend_entered_lobby.connect(self._on_friend_entered_lobby)
        tabs.addTab(self.friends_tab, "Друзья")

        self.status_tab = StatusTab()
        tabs.addTab(self.status_tab, "Status")

        self.players_tab = PlayersTab()
        tabs.addTab(self.players_tab, "Игроки")

        self.profile_tab = ProfileTab()
        tabs.addTab(self.profile_tab, "Профиль Steam")

        self.console_tab = ConsoleTab()
        self.console_tab.server_connected.connect(self.recent_tab.on_server_connected)
        self.console_tab.status_ready.connect(self._on_status_snapshot)
        tabs.addTab(self.console_tab, "Консоль")

        root.addWidget(tabs)

        self.setCentralWidget(central)


        self.timer = QTimer(self)
        self.timer.timeout.connect(self.lobbies_tab.refresh)
        self.timer.start(REFRESH_INTERVAL_MS)

        self._init_tray_icon()
        self._check_update_executor = ThreadPoolExecutor(max_workers=1)
        self.update_check_done.connect(self._on_update_check_done)
        QTimer.singleShot(0, self._check_for_update)

        self._notifications: list = []
        self._notifications_executor = ThreadPoolExecutor(max_workers=1)
        self.notifications_loaded.connect(self._on_notifications_loaded)
        self._notifications_timer = QTimer(self)
        self._notifications_timer.timeout.connect(self._load_notifications)
        self._notifications_timer.start(30 * 60 * 1000)
        self._load_notifications()

    update_check_done = pyqtSignal(object)
    notifications_loaded = pyqtSignal(list)

    def _check_for_update(self):
        self._check_update_executor.submit(self._check_update_worker)

    def _check_update_worker(self):
        try:
            info = check_for_update(__version__)
        except Exception:
            info = None
        self.update_check_done.emit(info)

    def _on_update_check_done(self, info):
        if info is None:
            return
        dialog = UpdateDialog(info, self)
        dialog.show()

    def _load_notifications(self):
        self._notifications_executor.submit(self._fetch_notifications_worker)

    def _fetch_notifications_worker(self):
        try:
            items = fetch_notifications()
        except Exception:
            items = []
        self.notifications_loaded.emit(items)

    def _on_notifications_loaded(self, items: list):
        self._notifications = items
        self.title_bar.set_notifications_state(has_unread(items))

    def show_notifications_popup(self, anchor_btn):
        popup = NotificationsPopup(self._notifications, anchor_btn, self)
        popup.finished.connect(
            lambda _: self.title_bar.set_notifications_state(
                has_unread(self._notifications)
            )
        )
        if has_unread(self._notifications):
            mark_all_read(self._notifications)
        popup.show()

    def _on_status_snapshot(self, snapshot):
        self.status_tab.apply_snapshot(snapshot)
        self.players_tab.reload()

    def _init_tray_icon(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon = None
            return

        icon = QIcon(str(ICON_ICO)) if ICON_ICO.exists() else self.windowIcon()
        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip("Shinri Trial Launcher")

        tray_menu = QMenu()
        show_action = QAction("Открыть лаунчер", self)
        show_action.triggered.connect(self._restore_from_tray)
        exit_action = QAction("Выход", self)
        exit_action.triggered.connect(self._quit_app)
        tray_menu.addAction(show_action)
        tray_menu.addSeparator()
        tray_menu.addAction(exit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _restore_from_tray(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _on_friend_entered_lobby(self, friend_name: str, lobby_name: str):
        if not load_config().get("friend_lobby_notifications", True):
            return
        if self.tray_icon is None:
            return
        self.tray_icon.showMessage(
            "Друг зашёл в лобби",
            f"{friend_name} зашёл в {lobby_name}",
            QSystemTrayIcon.MessageIcon.Information,
        )

    def _on_queue_connected(self, server_name: str, address: str):
        if self.tray_icon is not None:
            self.tray_icon.showMessage(
                "Место освободилось",
                f"Подключаемся к «{server_name}» ({address})",
                QSystemTrayIcon.MessageIcon.Information,
            )

    def _on_tray_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.DoubleClick,
            QSystemTrayIcon.ActivationReason.Trigger,
        ):
            self._restore_from_tray()

    def _quit_app(self):
        if self.tray_icon is not None:
            self.tray_icon.hide()
        self.lobbies_tab.shutdown()
        self.recent_tab.shutdown()
        self.friends_tab.shutdown()
        self.profile_tab.shutdown()
        self.status_tab.shutdown()
        self.players_tab.shutdown()
        self.console_tab.shutdown()
        self._notifications_executor.shutdown(wait=False, cancel_futures=True)
        QApplication.instance().quit()

    def closeEvent(self, event):
        config = load_config()
        if config.get("close_to_tray", False) and self.tray_icon is not None:
            event.ignore()
            self.hide()
            self.tray_icon.showMessage(
                "Shinri Trial Launcher",
                "Лаунчер свёрнут в трей и продолжает работать в фоне.",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )
        else:
            if self.tray_icon is not None:
                self.tray_icon.hide()
            self.lobbies_tab.shutdown()
            self.recent_tab.shutdown()
            self.friends_tab.shutdown()
            self.profile_tab.shutdown()
            self.status_tab.shutdown()
            self.players_tab.shutdown()
            self.console_tab.shutdown()
            self._notifications_executor.shutdown(wait=False, cancel_futures=True)
            event.accept()


def main():


    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ShinriTrial.Launcher")
        except Exception:
            pass

    app = QApplication(sys.argv)
    if ICON_ICO.exists():
        app.setWindowIcon(QIcon(str(ICON_ICO)))
    app.setStyleSheet(THEME_QSS)
    window = LauncherWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()