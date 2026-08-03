VOID_BLACK = "#0B0A08"
PANEL = "#18140F"
PANEL_EDGE = "#2E271D"
ACCENT = "#E8A93E"
ACCENT_LIGHT = "#F6CD82"
ACCENT_DARK = "#B9822A"
HOPE_GOLD = "#F2C879"
BONE = "#F2F0EA"
DIALOG_BG = "#050403"
DIALOG_HEADER = "#241C10"
ASH = "#948C7C"

DISPLAY_FONT = "Impact"
MONO_FONT = "Consolas"
UI_FONT = "Segoe UI"


THEME_QSS = f"""
QWidget {{
    background-color: {VOID_BLACK};
    color: {BONE};
    font-family: "{UI_FONT}";
    font-size: 13px;
}}
QLabel {{
    background-color: transparent;
}}

/* ---------- Внешняя рамка окна (компенсация потери системной рамки) ---------- */
QWidget#RootContainer {{
    border: 1px solid {PANEL_EDGE};
}}
QDialog#SettingsDialog {{
    background-color: {DIALOG_BG};
    border: 2px solid {ACCENT};
}}
QFrame#SettingsHeader {{
    background-color: {DIALOG_HEADER};
}}
QWidget#SettingsBody {{
    background-color: {DIALOG_BG};
}}
QLabel#SettingsSectionLabel {{
    color: {ACCENT};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2px;
}}
QCheckBox {{
    color: {BONE};
    font-size: 13px;
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {PANEL_EDGE};
    background-color: {PANEL};
}}
QCheckBox::indicator:hover {{
    border-color: {ACCENT};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
}}

/* ---------- Кастомный заголовок окна ---------- */
QFrame#TitleBar {{
    background-color: {PANEL};
    border-bottom: 1px solid {PANEL_EDGE};
}}
QLabel#TitleBarText {{
    color: {ASH};
    font-family: "{UI_FONT}";
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}}
QPushButton#TitleBarButton {{
    background-color: transparent;
    color: {ASH};
    border: none;
    border-top: none;
    border-radius: 0;
    font-size: 14px;
    font-weight: 400;
}}
QPushButton#TitleBarButton:hover {{
    background-color: {PANEL_EDGE};
    color: {BONE};
}}
QPushButton#TitleBarCloseButton {{
    background-color: transparent;
    color: {ASH};
    border: none;
    border-top: none;
    border-radius: 0;
    font-size: 13px;
    font-weight: 400;
}}
QPushButton#TitleBarCloseButton:hover {{
    background-color: #C0392B;
    color: {BONE};
}}

/* ---------- Заголовок приложения ---------- */
QLabel#AppTitle {{
    color: {BONE};
    font-family: "{DISPLAY_FONT}";
    font-size: 26px;
    letter-spacing: 2px;
    padding: 2px 0 0 0;
}}
QLabel#AppSubtitle {{
    color: {ASH};
    font-size: 11px;
    letter-spacing: 3px;
}}
QFrame#HeaderBar {{
    background-color: {PANEL};
    border-bottom: 3px solid {ACCENT};
}}
QFrame#AccentStrip {{
    background-color: {ACCENT};
    max-height: 3px;
    min-height: 3px;
}}

/* ---------- Вкладки ---------- */
QTabWidget::pane {{
    border: 1px solid {PANEL_EDGE};
    background-color: {VOID_BLACK};
}}
QTabBar::tab {{
    background-color: {PANEL};
    color: {ASH};
    border: 1px solid {PANEL_EDGE};
    border-bottom: none;
    padding: 9px 22px;
    margin-right: 2px;
    font-weight: 600;
    letter-spacing: 1px;
    font-size: 11px;
}}
QTabBar::tab:selected {{
    background-color: {VOID_BLACK};
    color: {BONE};
    border-top: 2px solid {ACCENT};
    border-bottom: none;
    padding-top: 8px;
}}
QTabBar::tab:hover:!selected {{
    color: {BONE};
    background-color: #201B14;
}}

/* ---------- Верхняя панель вкладки (счётчик + Обновить) ---------- */
QLabel#SummaryLabel {{
    color: {ASH};
    font-family: "{MONO_FONT}";
    font-size: 12px;
    letter-spacing: 1px;
}}
QLabel#HintLabel {{
    color: {ASH};
    font-style: italic;
    padding: 10px;
}}

/* ---------- Таблица серверов ---------- */
QTableWidget {{
    background-color: {PANEL};
    alternate-background-color: #1F1A13;
    gridline-color: {PANEL_EDGE};
    border: 1px solid {PANEL_EDGE};
    outline: none;
    font-family: "{MONO_FONT}";
    font-size: 12px;
}}
QTableWidget::item {{
    padding: 6px 8px;
    border: none;
    border-bottom: 1px solid {PANEL_EDGE};
    outline: none;
}}
QTableWidget::item:focus {{
    border: none;
    outline: none;
}}
QHeaderView::section {{
    background-color: {VOID_BLACK};
    color: {ASH};
    padding: 8px;
    border: none;
    border-bottom: 2px solid {ACCENT};
    border-right: 1px solid {PANEL_EDGE};
    font-family: "{UI_FONT}";
    font-weight: 600;
    font-size: 11px;
    letter-spacing: 1px;
}}

/* ---------- Кнопки ---------- */
QPushButton {{
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {ACCENT_LIGHT}, stop:0.18 {ACCENT}, stop:1 {ACCENT});
    color: {VOID_BLACK};
    border: none;
    border-top: 1px solid {ACCENT_LIGHT};
    padding: 4px 14px;
    font-weight: 700;
    font-size: 12px;
    letter-spacing: 0.5px;
    min-height: 24px;
}}
QPushButton:hover {{
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #FFE3AE, stop:0.18 {ACCENT_LIGHT}, stop:1 {ACCENT_LIGHT});
    color: {VOID_BLACK};
}}
QPushButton:pressed {{
    background-color: {ACCENT_DARK};
    border-top: 1px solid {ACCENT};
    color: {BONE};
}}
QPushButton:disabled {{
    background-color: #2E271D;
    border-top: 1px solid #2E271D;
    color: {ASH};
}}

/* Кнопка "Обновить" в верхней панели — второстепенная, не такая яркая */
QPushButton#RefreshButton {{
    background-color: transparent;
    color: {ASH};
    border: 1px solid {PANEL_EDGE};
    border-top: 1px solid {PANEL_EDGE};
    padding: 6px 14px;
}}
QPushButton#RefreshButton:hover {{
    color: {BONE};
    border-color: {ACCENT};
    border-top-color: {ACCENT};
    background-color: transparent;
}}

/* ---------- Большие кнопки главного меню (Главная, справа внизу) ---------- */
QPushButton#MenuButtonPrimary {{
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {ACCENT_LIGHT}, stop:0.22 {ACCENT}, stop:1 {ACCENT});
    color: {VOID_BLACK};
    border: none;
    border-top: 2px solid #FFE3AE;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 1px;
}}
QPushButton#MenuButtonPrimary:hover {{
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #FFE9BC, stop:0.22 {ACCENT_LIGHT}, stop:1 {ACCENT_LIGHT});
}}
QPushButton#MenuButtonPrimary:pressed {{
    background-color: {ACCENT_DARK};
    border-top: 2px solid {ACCENT};
    color: {BONE};
}}

/* ---------- Текстовые ссылки (Главная, левый нижний угол) ---------- */
QLabel#SocialLink {{
    font-size: 12px;
}}
QLabel#CreditLabel {{
    color: rgba(255, 255, 255, 90);
    font-size: 20px;
    font-weight: 700;
    font-style: italic;
}}

/* ---------- Выпадающий список (выбор основного лобби) ---------- */
QComboBox {{
    background-color: {PANEL};
    color: {BONE};
    border: 1px solid {PANEL_EDGE};
    border-top: 1px solid {ACCENT};
    padding: 4px 10px;
    font-size: 12px;
}}
QComboBox:hover {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox QAbstractItemView {{
    background-color: {PANEL};
    color: {BONE};
    border: 1px solid {ACCENT};
    selection-background-color: {ACCENT};
    selection-color: {VOID_BLACK};
    outline: none;
}}

/* ---------- Скроллбары ---------- */
QScrollBar:vertical {{
    background: {PANEL_EDGE};
    width: 10px;
}}
QScrollBar::handle:vertical {{
    background: {VOID_BLACK};
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {ACCENT};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
"""