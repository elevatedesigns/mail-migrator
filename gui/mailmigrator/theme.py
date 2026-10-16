"""Colours, icons and the Qt style sheet for Mail Migrator's dark theme."""

from __future__ import annotations

import os
import tempfile

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer

BG = "#0a1020"
SIDEBAR = "#0c1324"
CARD = "#0f1729"
CARD_INNER = "#121c33"
INPUT = "#0b1222"
BORDER = "#1e2a44"
BORDER_HI = "#2b3b60"
TEXT = "#e8eef9"
MUTED = "#9aa8c3"
DIM = "#6b7a96"
ACCENT = "#1677ff"
ACCENT_HI = "#3d8bff"
ACCENT_SOFT = "#13284f"
PURPLE = "#7c5cff"
GREEN = "#22c55e"
GREEN_DARK = "#16a34a"
RED = "#ef4444"
AMBER = "#f59e0b"

# Stroke icons in the style of the Lucide set (24x24 viewBox).
ICONS = {
    "mail": '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>',
    "send": '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
    "bookmark": '<path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "file": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    "help": '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>',
    "server": '<rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><path d="M6 6h.01"/><path d="M6 18h.01"/>',
    "box": '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>',
    "lock": '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "network": '<rect x="16" y="16" width="6" height="6" rx="1"/><rect x="2" y="16" width="6" height="6" rx="1"/><rect x="9" y="2" width="6" height="6" rx="1"/><path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3"/><path d="M12 12V8"/>',
    "user": '<circle cx="12" cy="8" r="5"/><path d="M20 21a8 8 0 0 0-16 0"/>',
    "at": '<circle cx="12" cy="12" r="4"/><path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-4 8"/>',
    "key": '<circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6"/><path d="m15.5 7.5 3 3L22 7l-3-3"/>',
    "wifi": '<path d="M12 20h.01"/><path d="M2 8.82a15 15 0 0 1 20 0"/><path d="M5 12.86a10 10 0 0 1 14 0"/><path d="M8.5 16.43a5 5 0 0 1 7 0"/>',
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    "bulb": '<path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"/><path d="M9 18h6"/><path d="M10 22h4"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "check-square": '<rect x="3" y="3" width="18" height="18" rx="4"/><path d="m8 12 3 3 5-6"/>',
    "folder": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
    "refresh": '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    "eye": '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
    "eye-off": '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/><path d="m3 3 18 18"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "arrow-left": '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
    "arrow-right": '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    "play": '<path d="M6 3 20 12 6 21Z"/>',
    "save": '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8"/><path d="M7 3v5h8"/>',
    "pencil": '<path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>',
    "chart": '<path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/>',
    "database": '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/>',
    "calendar": '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4"/><path d="M8 2v4"/><path d="M3 10h18"/>',
    "bell": '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
    "trash": '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
    "stop": '<rect x="5" y="5" width="14" height="14" rx="2"/>',
    "alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "shield": '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>',
    "filter": '<path d="M22 3H2l8 9.46V19l4 2v-8.54Z"/>',
    "external": '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    "folder-open": '<path d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-up": '<path d="m18 15-6-6-6 6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
}


def svg(name: str, color: str = TEXT, stroke: float = 2.0, fill: str = "none") -> bytes:
    body = ICONS[name]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{fill}" '
            f'stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
            f'stroke-linejoin="round">{body}</svg>').encode()


def pixmap(name: str, color: str = TEXT, size: int = 20, stroke: float = 2.0,
           ratio: float = 2.0, fill: str = "none") -> QPixmap:
    renderer = QSvgRenderer(QByteArray(svg(name, color, stroke, fill)))
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    renderer.render(painter, QRectF(0, 0, size * ratio, size * ratio))
    painter.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def icon(name: str, color: str = TEXT, size: int = 20) -> QIcon:
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size))
    ic.addPixmap(pixmap(name, DIM, size), QIcon.Disabled)
    return ic


_asset_dir: str | None = None


def _assets() -> str:
    """Small SVG files referenced from the style sheet (Qt QSS needs files)."""
    global _asset_dir
    if _asset_dir:
        return _asset_dir
    d = tempfile.mkdtemp(prefix="mailmigrator-theme-")
    files = {
        "chevron.svg": svg("chevron-down", MUTED, 2.5),
        "up.svg": svg("chevron-up", MUTED, 2.5),
        "down.svg": svg("chevron-down", MUTED, 2.5),
        "check.svg": svg("check", "#ffffff", 3.5),
        "radio.svg": (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20">'
                      b'<circle cx="10" cy="10" r="4.5" fill="#ffffff"/></svg>'),
    }
    for fname, data in files.items():
        with open(os.path.join(d, fname), "wb") as fh:
            fh.write(data)
    _asset_dir = d.replace(os.sep, "/")
    return _asset_dir


def palette() -> QPalette:
    p = QPalette()
    for role, color in (
        (QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, INPUT),
        (QPalette.AlternateBase, CARD_INNER), (QPalette.Text, TEXT),
        (QPalette.Button, CARD_INNER), (QPalette.ButtonText, TEXT),
        (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, "#ffffff"),
        (QPalette.ToolTipBase, CARD_INNER), (QPalette.ToolTipText, TEXT),
        (QPalette.PlaceholderText, DIM), (QPalette.Link, ACCENT_HI),
    ):
        p.setColor(role, QColor(color))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(DIM))
    return p


def stylesheet() -> str:
    a = _assets()
    return f"""
* {{ outline: none; }}
QWidget {{ color: {TEXT}; font-size: 14px; }}
QMainWindow, #root {{ background: {BG}; }}
QToolTip {{ background: {CARD_INNER}; color: {TEXT}; border: 1px solid {BORDER_HI};
            padding: 6px; border-radius: 6px; }}

#sidebar {{ background: {SIDEBAR}; border-right: 1px solid {BORDER}; }}
#appName {{ font-size: 20px; font-weight: 700; }}
#appVersion {{ color: {MUTED}; font-size: 13px; }}
QPushButton#nav {{ text-align: left; padding: 12px 16px; border: none; border-radius: 10px;
                   background: transparent; color: {TEXT}; font-size: 15px; }}
QPushButton#nav:hover {{ background: #131d35; }}
QPushButton#nav:checked {{ background: {ACCENT_SOFT}; border-left: 3px solid {ACCENT};
                           font-weight: 600; }}

#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 14px; }}
#inner {{ background: {CARD_INNER}; border: 1px solid {BORDER}; border-radius: 12px; }}
#infoBox {{ background: {CARD_INNER}; border: 1px solid {BORDER}; border-radius: 12px; }}
#divider {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}

QLabel#h1 {{ font-size: 28px; font-weight: 700; }}
QLabel#h2 {{ font-size: 19px; font-weight: 700; }}
QLabel#h3 {{ font-size: 16px; font-weight: 600; }}
QLabel#sub {{ color: {MUTED}; font-size: 15px; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#hint {{ color: {DIM}; font-size: 12px; }}
QLabel#link {{ color: {ACCENT_HI}; font-weight: 600; font-size: 15px; }}
QLabel#danger {{ color: {RED}; }}
QLabel#stat {{ font-size: 22px; font-weight: 700; }}

QLineEdit, QComboBox, QSpinBox, QDateEdit, QPlainTextEdit#field {{
    background: {INPUT}; border: 1px solid {BORDER_HI}; border-radius: 8px;
    padding: 8px 12px; min-height: 22px; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDateEdit:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDateEdit:disabled {{
    color: {DIM}; background: #0d1426; }}
QLineEdit[readOnly="true"] {{ color: {MUTED}; }}
QComboBox::drop-down, QDateEdit::drop-down {{ border: none; width: 34px; }}
QComboBox::down-arrow, QDateEdit::down-arrow {{ image: url({a}/chevron.svg); width: 16px; height: 16px; }}
QComboBox QAbstractItemView {{ background: {CARD_INNER}; border: 1px solid {BORDER_HI};
    selection-background-color: {ACCENT}; padding: 4px; outline: none; }}
QSpinBox::up-button, QSpinBox::down-button {{ border: none; width: 26px;
    border-left: 1px solid {BORDER_HI}; background: transparent; }}
QSpinBox::up-arrow {{ image: url({a}/up.svg); width: 12px; height: 12px; }}
QSpinBox::down-arrow {{ image: url({a}/down.svg); width: 12px; height: 12px; }}
QCalendarWidget QWidget {{ background: {CARD_INNER}; alternate-background-color: {CARD}; }}
QCalendarWidget QToolButton {{ background: transparent; color: {TEXT}; padding: 4px 8px; }}
QCalendarWidget QAbstractItemView:enabled {{ selection-background-color: {ACCENT}; }}

QCheckBox, QRadioButton {{ spacing: 12px; font-size: 14px; }}
QCheckBox::indicator {{ width: 20px; height: 20px; border-radius: 5px;
    border: 2px solid {BORDER_HI}; background: {INPUT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT};
    image: url({a}/check.svg); }}
QCheckBox::indicator:disabled {{ border-color: {BORDER}; }}
QRadioButton::indicator {{ width: 20px; height: 20px; border-radius: 11px;
    border: 2px solid {BORDER_HI}; background: {INPUT}; }}
QRadioButton::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT};
    image: url({a}/radio.svg); }}

QPushButton {{ background: {CARD_INNER}; border: 1px solid {BORDER_HI}; border-radius: 10px;
               padding: 10px 18px; font-size: 14px; font-weight: 500; }}
QPushButton:hover {{ background: #18243f; border-color: #3a4d78; }}
QPushButton:disabled {{ color: {DIM}; background: #0e1628; border-color: {BORDER}; }}
QPushButton#primary {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: white;
                       font-weight: 600; font-size: 16px; padding: 12px 26px; }}
QPushButton#primary:hover {{ background: {ACCENT_HI}; }}
QPushButton#primary:disabled {{ background: #1b3157; border-color: #1b3157; color: #7d8fb0; }}
QPushButton#go {{ background: {GREEN_DARK}; border: 1px solid {GREEN}; color: white;
                  font-weight: 600; font-size: 16px; padding: 12px 26px; }}
QPushButton#go:hover {{ background: {GREEN}; }}
QPushButton#go:disabled {{ background: #173826; border-color: #173826; color: #6f9c80; }}
QPushButton#danger {{ background: #3a1218; border: 1px solid #7f1d1d; color: #fecaca; }}
QPushButton#danger:hover {{ background: #4c1720; }}
QPushButton#ghost {{ font-size: 16px; padding: 12px 26px; }}
QPushButton#test {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: white;
                    font-weight: 600; padding: 9px 18px; }}
QPushButton#test:hover {{ background: {ACCENT_HI}; }}
QPushButton#small {{ padding: 6px 14px; font-size: 13px; }}
QToolButton#eye {{ background: transparent; border: none; padding: 4px; }}
QToolButton#disclosure {{ background: transparent; border: none; text-align: left; }}

QPlainTextEdit#log {{ background: #070c18; border: 1px solid {BORDER}; border-radius: 10px;
    padding: 8px; color: #c9d4ea; font-size: 12px; }}

QTableWidget {{ background: {INPUT}; border: 1px solid {BORDER_HI}; border-radius: 10px;
    gridline-color: {BORDER}; selection-background-color: {ACCENT_SOFT}; }}
QTableWidget::item {{ padding: 6px; }}
QHeaderView::section {{ background: {CARD_INNER}; color: {MUTED}; border: none;
    border-bottom: 1px solid {BORDER}; padding: 8px; font-weight: 600; }}
QTableCornerButton::section {{ background: {CARD_INNER}; border: none; }}

QProgressBar {{ background: #0b1426; border: none; border-radius: 6px; min-height: 12px;
                max-height: 12px; }}
QProgressBar::chunk {{ border-radius: 6px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
    stop:0 {ACCENT}, stop:1 {ACCENT_HI}); }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER_HI}; border-radius: 4px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER_HI}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QListWidget {{ background: {INPUT}; border: 1px solid {BORDER_HI}; border-radius: 10px; padding: 4px; }}
QListWidget::item {{ padding: 8px; border-radius: 6px; }}
QListWidget::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}
QMessageBox {{ background: {CARD}; }}
QMenu {{ background: {CARD_INNER}; border: 1px solid {BORDER_HI}; }}
QMenu::item:selected {{ background: {ACCENT}; }}
"""
