import urllib.error
import urllib.request
import time
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt6.QtGui import QPixmap, QDesktopServices
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QFrame, QComboBox, QPlainTextEdit, QApplication
)

import shinri_reviews_api as api
import player_lookup

# 10 минут кэша на поиск, чтобы не дёргать сервер при повторном вводе
_SEARCH_CACHE = {}
CACHE_TTL = 600 

class SearchWorker(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str, bool) # (текст ошибки, возможность повтора)
    not_found = pyqtSignal()

    def __init__(self, query: str):
        super().__init__()
        self.query = query.strip()

    def run(self):
        try:
            # 1. Определяем, что ввели: UID или ник
            if self.query.isdigit():
                uid = int(self.query)
            else:
                # Ищем по нику
                search_res = api.search(self.query)
                items = api._unwrap(search_res)
                if not items:
                    self.not_found.emit()
                    return
                
                # Ищем точное совпадение или берем первого
                uid = None
                for item in items:
                    norm = api._norm(item)
                    if norm and norm["name"].lower() == self.query.lower():
                        uid = norm["id"]
                        break
                if not uid and items:
                    norm = api._norm(items[0])
                    if norm: uid = norm["id"]
                
                if not uid:
                    self.not_found.emit()
                    return

            # 2. Загружаем профиль
            p = api.get_player(uid)
            if not p:
                self.not_found.emit()
                return

            # 3. История ников
            try:
                hist = api.nick_history(uid)
            except Exception:
                hist = {"history": []}

            # 4. Отзывы
            try:
                rev = api.reviews(uid)
            except Exception:
                rev = {}

            # 5. Аватарка
            avatar_id = p.get("avatarGameId")
            avatar_url = None
            avatar_bytes = None
            
            if avatar_id:
                amap = player_lookup.avatar_map()
                avatar_url = amap.get(avatar_id.lower())
                if avatar_url:
                    try:
                        req = urllib.request.Request(avatar_url, headers=api.HEADERS)
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            avatar_bytes = resp.read()
                    except Exception:
                        pass 

            stats = rev.get("stats") or {}
            result = {
                "id": uid,
                "name": p.get("name"),
                "profile_url": api.profile_url(uid),
                "avatarGameId": avatar_id,
                "avatar_url": avatar_url,
                "avatar_bytes": avatar_bytes,
                "nick_history": hist.get("history") or [],
                "reviews_count": stats.get("count", 0),
                "reviews_avg": stats.get("avg"),
                "reviews": rev.get("reviews") or [],
            }
            self.finished.emit(result)

        except urllib.error.HTTPError as e:
            if e.code == 404:
                self.not_found.emit()
            elif e.code in (403, 429):
                self.error.emit("Сервер временно ограничил доступ (403/429).\nПожалуйста, не спамьте запросами.", False)
            else:
                self.error.emit(f"Ошибка сервера: HTTP {e.code}", True)
        except Exception as e:
            self.error.emit(f"Ошибка сети: {str(e)}", True)


class ReviewWidget(QFrame):
    def __init__(self, review):
        super().__init__()
        self.setObjectName("ProfileCard")
        box = QVBoxLayout(self)
        box.setContentsMargins(12, 10, 12, 10)
        box.setSpacing(4)

        top = QHBoxLayout()
        
        rating = review.get("rating", 0)
        rating_lbl = QLabel(f"★ {rating}/5")
        rating_lbl.setStyleSheet("color: #F2C879; font-weight: bold; font-size: 14px;")
        top.addWidget(rating_lbl)

        author = review.get("authorNickname") or review.get("authorGameName") or "Аноним"
        if review.get("authorVerified"):
            author += " (проверен)"
        author_lbl = QLabel(author)
        author_lbl.setStyleSheet("color: #F2F0EA; font-weight: bold;")
        top.addWidget(author_lbl)
        
        top.addStretch()

        likes = review.get("likes", 0)
        if likes:
            likes_lbl = QLabel(f"👍 {likes}")
            likes_lbl.setStyleSheet("color: #948C7C;")
            top.addWidget(likes_lbl)

        date_str = player_lookup._date(review)
        if date_str:
            date_lbl = QLabel(date_str)
            date_lbl.setStyleSheet("color: #948C7C;")
            top.addWidget(date_lbl)

        box.addLayout(top)

        text = str(review.get("text") or "").strip()
        if text:
            text_lbl = QLabel(text)
            text_lbl.setWordWrap(True)
            text_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            box.addWidget(text_lbl)


class UidSearchTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._closed = False
        self._worker = None
        self._current_data = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)
        # Прижимаем все элементы к верху, чтобы поисковая строка не висела по центру
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # --- Верхняя панель поиска ---
        top_bar = QHBoxLayout()
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Введите UID (#3865) или ник игрока")
        self.input_field.returnPressed.connect(self._start_search)

        self.search_btn = QPushButton("Найти")
        self.search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_btn.clicked.connect(self._start_search)

        top_bar.addWidget(self.input_field, 1)
        top_bar.addWidget(self.search_btn)
        layout.addLayout(top_bar)

        # --- Статус (загрузка, ошибки) ---
        self.status_label = QLabel("")
        self.status_label.setObjectName("HintLabel")
        self.status_label.setWordWrap(True)
        self.status_label.setVisible(False)
        layout.addWidget(self.status_label)

        self.retry_btn = QPushButton("Повторить")
        self.retry_btn.setObjectName("RefreshButton")
        self.retry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.retry_btn.clicked.connect(self._start_search)
        self.retry_btn.setVisible(False)
        layout.addWidget(self.retry_btn, 0, Qt.AlignmentFlag.AlignLeft)

        # --- Область результатов ---
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setVisible(False)

        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(10)

        # 1. Карточка профиля
        self.profile_card = QFrame()
        self.profile_card.setObjectName("ProfileCard")
        pc_layout = QHBoxLayout(self.profile_card)
        
        self.avatar_lbl = QLabel("Нет\nаватарки")
        self.avatar_lbl.setFixedSize(96, 96)
        self.avatar_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_lbl.setStyleSheet("background-color: #18140F; border: 1px solid #2E271D; color: #948C7C;")
        pc_layout.addWidget(self.avatar_lbl)

        info_layout = QVBoxLayout()
        self.name_lbl = QLabel("—")
        self.name_lbl.setObjectName("ProfileName")
        self.name_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info_layout.addWidget(self.name_lbl)

        self.uid_lbl = QLabel("UID: —")
        self.uid_lbl.setObjectName("ProfileSubtitle")
        info_layout.addWidget(self.uid_lbl)

        self.avatar_id_lbl = QLabel("avatarGameId: —")
        self.avatar_id_lbl.setObjectName("FileSizeLabel")
        info_layout.addWidget(self.avatar_id_lbl)
        
        info_layout.addStretch()
        pc_layout.addLayout(info_layout, 1)

        btn_layout = QVBoxLayout()
        self.open_btn = QPushButton("Открыть в браузере")
        self.open_btn.setObjectName("RefreshButton")
        self.open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_btn.clicked.connect(self._open_browser)
        
        self.copy_btn = QPushButton("Копировать ник/UID")
        self.copy_btn.setObjectName("RefreshButton")
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.clicked.connect(self._copy_info)

        btn_layout.addWidget(self.open_btn)
        btn_layout.addWidget(self.copy_btn)
        pc_layout.addLayout(btn_layout)

        self.content_layout.addWidget(self.profile_card)

        # 2. Статистика и история ников
        self.stats_card = QFrame()
        self.stats_card.setObjectName("ProfileCard")
        sc_layout = QVBoxLayout(self.stats_card)
        
        self.stats_lbl = QLabel("Отзывы: 0 | Средняя оценка: —")
        self.stats_lbl.setObjectName("ProfileSubtitle")
        sc_layout.addWidget(self.stats_lbl)

        self.nick_hist_lbl = QLabel("Прошлые ники: —")
        self.nick_hist_lbl.setWordWrap(True)
        self.nick_hist_lbl.setStyleSheet("color: #948C7C; font-size: 12px;")
        sc_layout.addWidget(self.nick_hist_lbl)
        
        self.content_layout.addWidget(self.stats_card)

        # 3. Сортировка
        sort_layout = QHBoxLayout()
        sort_layout.addWidget(QLabel("Сортировка отзывов:"))
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Новые", "По лайкам", "По оценке"])
        self.sort_combo.currentIndexChanged.connect(self._render_reviews)
        sort_layout.addWidget(self.sort_combo)
        sort_layout.addStretch()
        self.content_layout.addLayout(sort_layout)

        # 4. Контейнер для отзывов
        self.reviews_container = QWidget()
        self.reviews_layout = QVBoxLayout(self.reviews_container)
        self.reviews_layout.setContentsMargins(0, 0, 0, 0)
        self.reviews_layout.setSpacing(8)
        self.content_layout.addWidget(self.reviews_container)
        
        self.content_layout.addStretch()
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)

    def _start_search(self):
        if self._closed:
            return
        text = self.input_field.text().strip().lstrip("#")
        if not text:
            self._show_error("Введите UID или ник игрока.", False)
            return

        # Проверка кэша (только для UID)
        if text.isdigit():
            uid = int(text)
            if uid in _SEARCH_CACHE:
                cached_time, data = _SEARCH_CACHE[uid]
                if time.time() - cached_time < CACHE_TTL:
                    self._on_success(data)
                    return

        self.status_label.setText("Загрузка данных...")
        self.status_label.setStyleSheet("color: #F2C879; font-size: 12px; padding: 10px;")
        self.status_label.setVisible(True)
        self.retry_btn.setVisible(False)
        self.scroll.setVisible(False)
        self.search_btn.setEnabled(False)

        self._worker = SearchWorker(text)
        self._worker.finished.connect(lambda d: self._on_success(d, cache=True))
        self._worker.not_found.connect(self._on_not_found)
        self._worker.error.connect(self._show_error)
        self._worker.start()

    def _on_success(self, data, cache=False):
        if self._closed:
            return
        if cache and data.get("id"):
            _SEARCH_CACHE[data["id"]] = (time.time(), data)

        self._current_data = data
        self.search_btn.setEnabled(True)
        self.status_label.setVisible(False)
        self.retry_btn.setVisible(False)
        self.scroll.setVisible(True)

        # Профиль
        self.name_lbl.setText(data.get("name") or "—")
        self.uid_lbl.setText(f"UID: {data.get('id')}")
        self.avatar_id_lbl.setText(f"avatarGameId: {data.get('avatarGameId') or '—'}")
        
        if data.get("avatar_bytes"):
            pixmap = QPixmap()
            if pixmap.loadFromData(data["avatar_bytes"]):
                self.avatar_lbl.setPixmap(pixmap.scaled(
                    96, 96, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                ))
            else:
                self.avatar_lbl.setText("Ошибка\nкартинки")
        else:
            self.avatar_lbl.setPixmap(QPixmap())
            self.avatar_lbl.setText("Нет\nаватарки")

        # Статистика
        cnt = data.get("reviews_count", 0)
        avg = data.get("reviews_avg")
        avg_str = f"{avg:.1f}" if avg is not None else "—"
        self.stats_lbl.setText(f"Отзывы: {cnt} | Средняя оценка: {avg_str}")
        
        hist = data.get("nick_history") or []
        if hist:
            hist_str = ", ".join([str(h) for h in hist if h])
            self.nick_hist_lbl.setText(f"Прошлые ники: {hist_str}")
        else:
            self.nick_hist_lbl.setText("Прошлые ники: нет")

        self._render_reviews()

    def _on_not_found(self):
        if self._closed:
            return
        self.search_btn.setEnabled(True)
        self.scroll.setVisible(False)
        self._show_error("Игрок не найден.", False)

    def _show_error(self, text, allow_retry):
        if self._closed:
            return
        self.search_btn.setEnabled(True)
        self.status_label.setText(text)
        self.status_label.setStyleSheet("color: #E8735E; font-size: 12px; padding: 10px;")
        self.status_label.setVisible(True)
        self.retry_btn.setVisible(allow_retry)

    def _render_reviews(self):
        if not self._current_data:
            return

        # Очистка
        while self.reviews_layout.count():
            item = self.reviews_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        reviews = self._current_data.get("reviews") or []
        if not reviews:
            empty = QLabel("У игрока пока нет отзывов.")
            empty.setStyleSheet("color: #948C7C; font-style: italic; padding: 10px;")
            self.reviews_layout.addWidget(empty)
            return

        # Сортировка
        sort_mode = self.sort_combo.currentIndex()
        if sort_mode == 0: # Новые
            reviews.sort(key=lambda r: player_lookup._date(r) or "", reverse=True)
        elif sort_mode == 1: # По лайкам
            reviews.sort(key=lambda r: r.get("likes", 0), reverse=True)
        elif sort_mode == 2: # По оценке
            reviews.sort(key=lambda r: r.get("rating", 0), reverse=True)

        for r in reviews:
            self.reviews_layout.addWidget(ReviewWidget(r))

    def _open_browser(self):
        if self._current_data:
            QDesktopServices.openUrl(QUrl(self._current_data["profile_url"]))

    def _copy_info(self):
        if self._current_data:
            text = f"{self._current_data.get('name')} (UID: {self._current_data.get('id')})"
            QApplication.clipboard().setText(text)

    def shutdown(self):
        self._closed = True
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait()