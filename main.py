import sys
import os
import webbrowser
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QUrl, QPoint
from PyQt6.QtGui import QColor, QFont, QIcon, QPixmap, QAction
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QTableWidget, QTableWidgetItem, QPushButton, QLabel,
    QHeaderView, QAbstractItemView, QFrame, QScrollArea, QComboBox, QDialog,
    QCheckBox, QSystemTrayIcon, QMenu
)

from servers import LOBBIES
from server_query import query_server, ServerStatus
from launcher_config import load_config, save_config, add_recent_server
from theme import THEME_QSS, HOPE_GOLD, ACCENT, ASH, BONE
from version import __version__
from update_checker import check_for_update

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
ICON_ICO = ASSETS_DIR / "icon.ico"
ICON_PNG = ASSETS_DIR / "icon.png"

DISCORD_URL = "https://discord.com/invite/shinri-trial"
OFFICIAL_SITE_URL = "https://shinri-trial.ru/"


REFRESH_INTERVAL_MS = 5_000


def connect_to_server(ip: str, port: int, name: str = ""):
    webbrowser.open(f"steam://connect/{ip}:{port}")
    display_name = name or f"{ip}:{port}"
    config = load_config()
    config["last_server"] = {"ip": ip, "port": port, "name": display_name}
    save_config(config)
    add_recent_server(ip, port, display_name)


class HomeTab(QWidget):

    play_result_ready = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.executor = ThreadPoolExecutor(max_workers=8)
        self.play_result_ready.connect(self._on_play_result)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)

        title = QLabel("Добро пожаловать на Shinri Trial")
        title.setObjectName("AppTitle")
        subtitle = QLabel(
            "Это неофициальный лаунчер, сделанный сообществом игроков. "
            "Он не имеет прямого отношения к команде разработки Shinri Trial "
            "и не является официальным продуктом проекта."
        )
        subtitle.setObjectName("HintLabel")
        subtitle.setWordWrap(True)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(20)
        layout.addLayout(self._build_useful_links())
        layout.addStretch()
        layout.addLayout(self._build_bottom_bar())

    def _build_useful_links(self) -> QVBoxLayout:
        block = QVBoxLayout()
        block.setSpacing(8)

        section_label = QLabel("ПОЛЕЗНЫЕ ССЫЛКИ")
        section_label.setObjectName("SettingsSectionLabel")
        block.addWidget(section_label)

        links_row = QHBoxLayout()
        links_row.setSpacing(8)

        useful_links = [
            ("Kirigiri's Press", "https://www.kirigiris.press/guidebook"),
            ("Отзывы игроков", "https://shinrireviews.com/"),
            ("Аддоны в Workshop", "https://steamcommunity.com/sharedfiles/filedetails/?id=3691631101"),
        ]
        for label, url in useful_links:
            btn = QPushButton(label)
            btn.setObjectName("RefreshButton")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, u=url: webbrowser.open(u))
            links_row.addWidget(btn)

        links_row.addStretch()
        block.addLayout(links_row)
        return block

    def _build_bottom_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.addLayout(self._build_social_links())
        bar.addStretch()

        self.play_btn = QPushButton("ИГРАТЬ")
        self.play_btn.setObjectName("MenuButtonPrimary")
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.setFixedSize(160, 44)
        self.play_btn.clicked.connect(self._on_play_clicked)
        bar.addWidget(self.play_btn)

        return bar

    def _build_social_links(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        col = QVBoxLayout()
        col.setSpacing(4)

        link_style = f"color: {ASH}; text-decoration: none;"

        site_link = QLabel(
            f'<a href="{OFFICIAL_SITE_URL}" style="{link_style}">Официальный сайт проекта</a>'
        )
        site_link.setObjectName("SocialLink")
        site_link.setOpenExternalLinks(True)
        site_link.setCursor(Qt.CursorShape.PointingHandCursor)

        discord_link = QLabel(
            f'<a href="{DISCORD_URL}" style="{link_style}">Официальный Discord-канал проекта</a>'
        )
        discord_link.setObjectName("SocialLink")
        discord_link.setOpenExternalLinks(True)
        discord_link.setCursor(Qt.CursorShape.PointingHandCursor)

        col.addWidget(site_link)
        col.addWidget(discord_link)

        credit_label = QLabel("by derpy")
        credit_label.setObjectName("CreditLabel")

        row.addLayout(col)
        row.addWidget(credit_label, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    def _on_play_clicked(self):
        if not LOBBIES:
            return
        self.play_btn.setEnabled(False)
        self.play_btn.setText("ПОИСК...")
        self.executor.submit(self._find_lobby_worker)

    def _find_lobby_worker(self):
        favorite_address = load_config().get("favorite_lobby")

        candidates = list(LOBBIES)
        if favorite_address:

            candidates.sort(key=lambda t: 0 if f"{t[0]}:{t[1]}" == favorite_address else 1)

        chosen = None
        for ip, port, name in candidates:
            status = query_server(ip, port, name, timeout=2.0)
            is_full = status.online and status.max_players > 0 and status.players >= status.max_players
            if status.online and not is_full:
                chosen = (ip, port, status.display_name)
                break

        self.play_result_ready.emit(chosen)

    def _on_play_result(self, chosen):
        self.play_btn.setEnabled(True)
        if chosen:
            ip, port, name = chosen
            self.play_btn.setText("ИГРАТЬ")
            connect_to_server(ip, port, name)
        else:
            self.play_btn.setText("НЕТ МЕСТ")
            QTimer.singleShot(2000, lambda: self.play_btn.setText("ИГРАТЬ"))


class ServerTab(QWidget):

    COLUMNS = ["Название", "Карта", "Игроки", "Пинг", "Статус", ""]


    result_ready = pyqtSignal(int, object)

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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        top_bar = QHBoxLayout()
        refresh_btn = QPushButton("⟳  Обновить")
        refresh_btn.setObjectName("RefreshButton")
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.clicked.connect(self.refresh)
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
        for col, width in ((1, 130), (2, 90), (3, 90), (4, 130), (5, 150)):
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
        for row, (ip, port, name) in enumerate(self.server_defs):
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

    def refresh(self):
        for row, (ip, port, name) in enumerate(self.server_defs):
            future = self.executor.submit(query_server, ip, port, name)
            future.add_done_callback(
                lambda f, row=row, ip=ip, port=port: self._on_result(row, ip, port, f.result())
            )

    def _handle_connect_click(self, ip: str, port: int):
        status = self.statuses.get((ip, port))
        name = status.display_name if status else f"{ip}:{port}"
        connect_to_server(ip, port, name)

        self.refresh()

    def _on_result(self, row: int, ip: str, port: int, status: ServerStatus):
        self.statuses[(ip, port)] = status


        self.result_ready.emit(row, status)

    def _apply_row(self, row: int, status: ServerStatus):
        self.table.setItem(row, 0, QTableWidgetItem(status.display_name))
        self.table.setItem(row, 1, QTableWidgetItem(status.map_name or "—"))

        is_full = status.online and status.max_players > 0 and status.players >= status.max_players

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
        self.table.setItem(row, 4, state_item)


        btn = self.buttons.get(row)
        if btn is not None:
            btn.setEnabled(not is_full)
            btn.setText("Сервер полон" if is_full else "Подключиться")


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


class SettingsDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setObjectName("SettingsDialog")
        self.setFixedSize(620, 600)
        self.recenter_on_parent()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
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

        body = QWidget()
        body.setObjectName("SettingsBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(28, 24, 28, 24)
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


        fav_section = QLabel("ИЗБРАННОЕ ЛОББИ")
        fav_section.setObjectName("SettingsSectionLabel")
        body_layout.addWidget(fav_section)

        self.fav_combo = QComboBox()
        self.fav_combo.setFixedHeight(36)
        self._populate_favorite_combo()
        self.fav_combo.currentIndexChanged.connect(self._on_favorite_changed)
        body_layout.addWidget(self.fav_combo)

        fav_hint = QLabel(
            "Лаунчер будет приоритетно подключать тебя именно на это "
            "лобби. Если оно окажется занятым — предложит любое другое "
            "свободное вместо него."
        )
        fav_hint.setObjectName("HintLabel")
        fav_hint.setWordWrap(True)
        body_layout.addWidget(fav_hint)

        body_layout.addSpacing(16)

        body_layout.addStretch()
        outer.addWidget(body, 1)

    def recenter_on_parent(self):
        parent = self.parent()
        if parent is not None:
            geo = parent.geometry()
            self.move(
                geo.x() + (geo.width() - self.width()) // 2,
                geo.y() + (geo.height() - self.height()) // 2,
            )

    def _populate_favorite_combo(self):
        config = load_config()
        saved_address = config.get("favorite_lobby")

        self.fav_combo.blockSignals(True)
        self.fav_combo.clear()
        self.fav_combo.addItem("Не выбрано", userData=None)
        for ip, port, name in LOBBIES:
            self.fav_combo.addItem(name, userData=(ip, port))

        if saved_address:
            for i in range(self.fav_combo.count()):
                data = self.fav_combo.itemData(i)
                if data and f"{data[0]}:{data[1]}" == saved_address:
                    self.fav_combo.setCurrentIndex(i)
                    break
        self.fav_combo.blockSignals(False)

    def _on_favorite_changed(self, index: int):
        data = self.fav_combo.itemData(index)
        config = load_config()
        if data:
            ip, port = data
            config["favorite_lobby"] = f"{ip}:{port}"
        else:
            config.pop("favorite_lobby", None)
        save_config(config)

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

        layout.addWidget(settings_btn)
        layout.addWidget(min_btn)
        layout.addWidget(close_btn)

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
        self.setFixedSize(920, 600)

        central = QWidget()
        central.setObjectName("RootContainer")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(TitleBar(self))
        root.addSpacing(4)

        tabs = QTabWidget()
        self.home_tab = HomeTab()
        tabs.addTab(self.home_tab, "Главная")
        self.lobbies_tab = ServerTab(
            LOBBIES,
            empty_hint=(
                "Список лобби пуст. Впиши реальные сервера в servers.py "
                "(список LOBBIES) — формат: (ip, port, название)."
            ),
        )
        tabs.addTab(self.lobbies_tab, "Лобби")
        root.addWidget(tabs)

        self.setCentralWidget(central)


        self.timer = QTimer(self)
        self.timer.timeout.connect(self.lobbies_tab.refresh)
        self.timer.start(REFRESH_INTERVAL_MS)

        self._init_tray_icon()
        self._check_update_executor = ThreadPoolExecutor(max_workers=1)
        self.update_check_done.connect(self._on_update_check_done)
        QTimer.singleShot(0, self._check_for_update)

    update_check_done = pyqtSignal(object)

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

    def _on_tray_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.DoubleClick,
            QSystemTrayIcon.ActivationReason.Trigger,
        ):
            self._restore_from_tray()

    def _quit_app(self):
        if self.tray_icon is not None:
            self.tray_icon.hide()
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