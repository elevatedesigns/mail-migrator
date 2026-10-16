"""Reusable widgets for the dark wizard interface."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QLinearGradient, QPainter,
                           QPen, QTextCharFormat, QTextCursor)
from PySide6.QtWidgets import (QAbstractButton, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout,
                               QWidget)

from . import theme as t


def label(text: str = "", obj: str = "", wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if obj:
        lb.setObjectName(obj)
    lb.setWordWrap(wrap)
    return lb


def divider() -> QFrame:
    f = QFrame()
    f.setObjectName("divider")
    f.setFrameShape(QFrame.NoFrame)
    return f


def card(obj: str = "card") -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName(obj)
    lay = QVBoxLayout(f)
    lay.setContentsMargins(24, 22, 24, 22)
    lay.setSpacing(14)
    return f, lay


def button(text: str, icon_name: str = "", obj: str = "", icon_color: str = t.TEXT,
           icon_right: bool = False) -> QPushButton:
    b = QPushButton(text)
    if obj:
        b.setObjectName(obj)
    if icon_name:
        b.setIcon(t.icon(icon_name, icon_color, 18))
        b.setIconSize(QSize(18, 18))
        if icon_right:
            b.setLayoutDirection(Qt.RightToLeft)
    b.setCursor(Qt.PointingHandCursor)
    return b


class IconBadge(QWidget):
    """Rounded gradient square with a white line icon, as in the mockups."""

    def __init__(self, icon_name: str, color: str = t.ACCENT, size: int = 64,
                 parent=None) -> None:
        super().__init__(parent)
        self._icon = icon_name
        self._color = QColor(color)
        self._size = size
        self.setFixedSize(size, size)

    def set_icon(self, icon_name: str, color: str | None = None) -> None:
        self._icon = icon_name
        if color:
            self._color = QColor(color)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0, 0, self._size, self._size)
        grad = QLinearGradient(r.topLeft(), r.bottomRight())
        grad.setColorAt(0, self._color.lighter(125))
        grad.setColorAt(1, self._color.darker(115))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(r, self._size * 0.24, self._size * 0.24)
        s = int(self._size * 0.5)
        pm = t.pixmap(self._icon, "#ffffff", s, 2.0)
        p.drawPixmap(int((self._size - s) / 2), int((self._size - s) / 2), pm)


class IconLabel(QLabel):
    def __init__(self, icon_name: str, color: str = t.MUTED, size: int = 20, parent=None):
        super().__init__(parent)
        self.setPixmap(t.pixmap(icon_name, color, size))
        self.setFixedSize(size + 4, size + 4)
        self.setAlignment(Qt.AlignCenter)

    def set_icon(self, icon_name: str, color: str, size: int = 20) -> None:
        self.setPixmap(t.pixmap(icon_name, color, size))


class PageHeader(QWidget):
    def __init__(self, icon_name: str, title: str, subtitle: str, color: str = t.ACCENT,
                 parent=None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(22)
        self.badge = IconBadge(icon_name, color, 72)
        lay.addWidget(self.badge, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(6)
        self.title = label(title, "h1", True)
        self.subtitle = label(subtitle, "sub", True)
        col.addWidget(self.title)
        col.addWidget(self.subtitle)
        col.addStretch(1)
        lay.addLayout(col, 1)


class Stepper(QWidget):
    """Numbered steps joined by lines. Completed steps show a tick."""

    step_clicked = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.steps: list[str] = []
        self.current = 0
        self.done_upto = -1     # highest step index that is complete
        self.setMinimumHeight(92)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)

    def set_steps(self, steps: list[str]) -> None:
        self.steps = steps
        self.update()

    def set_current(self, index: int, done_upto: int | None = None) -> None:
        self.current = index
        if done_upto is not None:
            self.done_upto = done_upto
        self.update()

    def _centers(self) -> list[QPointF]:
        n = max(1, len(self.steps))
        margin = 70
        span = max(1, self.width() - 2 * margin)
        return [QPointF(margin + (span * i / (n - 1) if n > 1 else span / 2), 30)
                for i in range(n)]

    def mousePressEvent(self, event) -> None:  # noqa: N802
        for i, c in enumerate(self._centers()):
            if (event.position() - c).manhattanLength() < 40 and i <= self.done_upto + 1:
                self.step_clicked.emit(i)
                return

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        centers = self._centers()
        radius = 22
        for i in range(len(centers) - 1):
            a, b = centers[i], centers[i + 1]
            active = i < self.current or i <= self.done_upto
            p.setPen(QPen(QColor(t.ACCENT if active else t.BORDER_HI), 2))
            p.drawLine(QPointF(a.x() + radius + 10, a.y()), QPointF(b.x() - radius - 10, b.y()))
        font = QFont(self.font())
        for i, (c, name) in enumerate(zip(centers, self.steps)):
            is_current = i == self.current
            is_done = i <= self.done_upto and not is_current
            if is_current:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(22, 119, 255, 60))
                p.drawEllipse(c, radius + 5, radius + 5)
            p.setBrush(QColor(t.ACCENT if (is_current or is_done) else "#1a2440"))
            p.setPen(QPen(QColor(t.ACCENT_HI if is_current else
                                 (t.ACCENT if is_done else t.BORDER_HI)), 2))
            p.drawEllipse(c, radius, radius)
            if is_done:
                pm = t.pixmap("check", "#ffffff", 20, 3)
                p.drawPixmap(int(c.x() - 10), int(c.y() - 10), pm)
            else:
                font.setPixelSize(18)
                font.setBold(True)
                p.setFont(font)
                p.setPen(QColor("#ffffff"))
                p.drawText(QRectF(c.x() - radius, c.y() - radius, 2 * radius, 2 * radius),
                           Qt.AlignCenter, str(i + 1))
            font.setPixelSize(15)
            font.setBold(is_current)
            p.setFont(font)
            p.setPen(QColor(t.TEXT if is_current else t.MUTED))
            p.drawText(QRectF(c.x() - 90, c.y() + radius + 10, 180, 24), Qt.AlignCenter, name)


class ChoiceCard(QFrame):
    """Large selectable option with a radio dot, as on the first wizard step."""

    clicked = Signal()

    def __init__(self, icon_name: str, color: str, title: str, text: str,
                 bullets: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("choice")
        self.setCursor(Qt.PointingHandCursor)
        self._selected = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 26, 24, 26)
        lay.setSpacing(16)
        top = QHBoxLayout()
        top.setSpacing(24)
        top.addWidget(IconBadge(icon_name, color, 76), 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(8)
        col.addWidget(label(title, "h2", True))
        col.addWidget(label(text, "sub", True))
        top.addLayout(col, 1)
        self.radio = QLabel()
        self.radio.setFixedSize(26, 26)
        top.addWidget(self.radio, 0, Qt.AlignTop)
        lay.addLayout(top)
        lay.addWidget(divider())
        lay.addWidget(label("Great for:", "h3"))
        for b in bullets:
            row = QHBoxLayout()
            row.setSpacing(12)
            row.addWidget(IconLabel("check", t.TEXT, 18))
            row.addWidget(label(b, wrap=True), 1)
            lay.addLayout(row)
        lay.addStretch(1)
        self.set_selected(False)

    def set_selected(self, on: bool) -> None:
        self._selected = on
        border = t.ACCENT if on else t.BORDER
        bg = "#0f2147" if on else t.CARD_INNER
        self.setStyleSheet(f"#choice {{ background: {bg}; border: 2px solid {border}; "
                           f"border-radius: 14px; }}")
        dot = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 26 26">'
               + (b'<circle cx="13" cy="13" r="11" fill="none" stroke="#3d8bff" stroke-width="2"/>'
                  b'<circle cx="13" cy="13" r="6" fill="#3d8bff"/>' if on else
                  b'<circle cx="13" cy="13" r="11" fill="none" stroke="#6b7a96" stroke-width="2"/>')
               + b'</svg>')
        from PySide6.QtCore import QByteArray
        from PySide6.QtGui import QPixmap
        from PySide6.QtSvg import QSvgRenderer
        pm = QPixmap(52, 52)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        QSvgRenderer(QByteArray(dot)).render(painter)
        painter.end()
        pm.setDevicePixelRatio(2)
        self.radio.setPixmap(pm)

    def mousePressEvent(self, _event) -> None:  # noqa: N802
        self.clicked.emit()


class Toggle(QAbstractButton):
    """iOS-style switch."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(46, 26)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(46, 26)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(t.ACCENT if on else "#2a3654"))
        if not self.isEnabled():
            p.setOpacity(0.5)
        p.drawRoundedRect(QRectF(0, 0, 46, 26), 13, 13)
        p.setBrush(QColor("#ffffff"))
        x = 23 if on else 3
        p.drawEllipse(QRectF(x, 3, 20, 20))


class CheckItem(QWidget):
    """A line with a round status icon: ok, fail, pending, running or neutral."""

    def __init__(self, text: str, state: str = "ok", parent=None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(12)
        self.icon = QLabel()
        self.icon.setFixedSize(24, 24)
        self.text = label(text, wrap=True)
        lay.addWidget(self.icon, 0, Qt.AlignTop)
        lay.addWidget(self.text, 1)
        self.set_state(state)

    def set_state(self, state: str, text: str | None = None) -> None:
        if text is not None:
            self.text.setText(text)
        colors = {"ok": t.GREEN, "fail": t.RED, "pending": "#3a4766",
                  "running": t.AMBER, "info": t.ACCENT}
        glyph = {"ok": "check", "fail": "x", "pending": "check", "running": "refresh",
                 "info": "check"}[state]
        size = 24
        from PySide6.QtGui import QPixmap
        canvas = QPixmap(size * 2, size * 2)
        canvas.fill(Qt.transparent)
        canvas.setDevicePixelRatio(2)
        p = QPainter(canvas)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(colors[state]))
        p.drawEllipse(QRectF(1, 1, size - 2, size - 2))
        pm = t.pixmap(glyph, "#ffffff", 14, 3)
        p.drawPixmap(5, 5, pm)
        p.end()
        self.icon.setPixmap(canvas)


class InfoCard(QFrame):
    """'Step 2 of 5' explanation card in the right column."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(14)
        top = QHBoxLayout()
        top.setSpacing(16)
        self.badge = IconBadge("info", t.ACCENT, 48)
        top.addWidget(self.badge)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = label("", "h3")
        self.subtitle = label("", "sub")
        col.addWidget(self.title)
        col.addWidget(self.subtitle)
        top.addLayout(col, 1)
        lay.addLayout(top)
        self.body = label("", "muted", True)
        lay.addWidget(self.body)

    def set(self, title: str, subtitle: str, body: str) -> None:
        self.title.setText(title)
        self.subtitle.setText(subtitle)
        self.body.setText(body)


class ChecklistCard(QFrame):
    """'Tips' or 'Ready to migrate' card: a titled list of CheckItems."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(12)
        top = QHBoxLayout()
        top.setSpacing(16)
        self.badge = IconBadge("bulb", t.ACCENT, 48)
        top.addWidget(self.badge)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = label("", "h3", True)
        self.subtitle = label("", "muted", True)
        col.addWidget(self.title)
        col.addWidget(self.subtitle)
        top.addLayout(col, 1)
        lay.addLayout(top)
        self.items_box = QVBoxLayout()
        self.items_box.setSpacing(8)
        lay.addLayout(self.items_box)
        self.items: list[CheckItem] = []

    def set(self, title: str, items: list[tuple[str, str]], icon_name: str = "bulb",
            color: str = t.ACCENT, subtitle: str = "") -> None:
        self.title.setText(title)
        self.subtitle.setText(subtitle)
        self.subtitle.setVisible(bool(subtitle))
        self.badge.set_icon(icon_name, color)
        while self.items:
            w = self.items.pop()
            self.items_box.removeWidget(w)
            w.hide()
            w.deleteLater()
        for text, state in items:
            item = CheckItem(text, state)
            self.items_box.addWidget(item)
            self.items.append(item)


class LogCard(QFrame):
    """'Migration log' card with timestamped lines and a Clear button."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(IconLabel("file", t.ACCENT_HI, 22))
        top.addWidget(label("Migration log", "h3"), 1)
        self.clear_btn = button("Clear", obj="small")
        top.addWidget(self.clear_btn)
        lay.addLayout(top)
        self.view = QPlainTextEdit()
        self.view.setObjectName("log")
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(60000)
        self.view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        mono.setPointSizeF(max(10.0, mono.pointSizeF() - 1))
        self.view.setFont(mono)
        self.view.setMinimumHeight(170)
        lay.addWidget(self.view, 1)
        self.clear_btn.clicked.connect(self.view.clear)
        # Follow new output unless the user scrolled up to read something.
        self._follow = True
        sb = self.view.verticalScrollBar()
        sb.valueChanged.connect(lambda v: setattr(self, "_follow", v >= sb.maximum() - 4))
        sb.rangeChanged.connect(lambda _lo, hi: self._follow and sb.setValue(hi))

    def add(self, text: str, kind: str = "") -> None:
        fmt = QTextCharFormat()
        stamp_fmt = QTextCharFormat()
        stamp_fmt.setForeground(QColor(t.DIM))
        if kind == "error":
            fmt.setForeground(QColor("#f87171"))
        elif kind == "warning":
            fmt.setForeground(QColor("#fbbf24"))
        elif kind == "ok":
            fmt.setForeground(QColor("#4ade80"))
        elif kind == "heading":
            fmt.setForeground(QColor(t.ACCENT_HI))
            fmt.setFontWeight(QFont.Bold)
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.End)
        if not self.view.document().isEmpty():
            cursor.insertText("\n")
        cursor.insertText(datetime.now().strftime("%H:%M:%S") + "  ", stamp_fmt)
        cursor.insertText(text, fmt)


class StatusLine(QFrame):
    """Rounded box with a coloured dot and a message (connection test result)."""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("inner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(14)
        self.dot = QLabel()
        self.dot.setFixedSize(18, 18)
        self.text = label(text, "muted", True)
        lay.addWidget(self.dot, 0, Qt.AlignTop)
        lay.addWidget(self.text, 1)
        self.set_state("idle", text)

    def set_state(self, state: str, text: str) -> None:
        color = {"idle": "#55627d", "running": t.AMBER, "ok": t.GREEN, "fail": t.RED}[state]
        self.dot.setStyleSheet(f"background: {color}; border-radius: 9px;")
        self.text.setText(text)
        self.text.setStyleSheet(
            f"color: {t.MUTED if state in ('idle', 'running') else color};")


class FormGrid(QGridLayout):
    """Icon · label · field rows with an optional hint under the field."""

    def __init__(self) -> None:
        super().__init__()
        self.setHorizontalSpacing(16)
        self.setVerticalSpacing(6)
        self.setColumnMinimumWidth(0, 24)
        self.setColumnMinimumWidth(1, 110)
        self.setColumnStretch(2, 1)
        self._row = 0
        self.rows: dict[str, list[QWidget]] = {}

    def add(self, key: str, icon_name: str, text: str, field: QWidget | QHBoxLayout,
            hint: str = "") -> QLabel | None:
        widgets: list[QWidget] = []
        ic = IconLabel(icon_name, t.MUTED, 22)
        lb = label(text)
        self.addWidget(ic, self._row, 0, Qt.AlignVCenter)
        self.addWidget(lb, self._row, 1, Qt.AlignVCenter)
        widgets += [ic, lb]
        if isinstance(field, QWidget):
            self.addWidget(field, self._row, 2)
            widgets.append(field)
        else:
            holder = QWidget()
            holder.setLayout(field)
            field.setContentsMargins(0, 0, 0, 0)
            self.addWidget(holder, self._row, 2)
            widgets.append(holder)
        self._row += 1
        hint_label = None
        if hint:
            hint_label = label(hint, "hint", True)
            self.addWidget(hint_label, self._row, 2)
            widgets.append(hint_label)
            self._row += 1
        spacer = QWidget()
        spacer.setFixedHeight(8)
        self.addWidget(spacer, self._row, 0)
        widgets.append(spacer)
        self._row += 1
        self.rows[key] = widgets
        return hint_label

    def add_widget(self, widget: QWidget) -> None:
        self.addWidget(widget, self._row, 0, 1, 3)
        self._row += 1

    def set_visible(self, key: str, visible: bool) -> None:
        for w in self.rows.get(key, []):
            w.setVisible(visible)


class StatTile(QWidget):
    def __init__(self, icon_name: str, caption: str, parent=None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        lay.addWidget(IconLabel(icon_name, t.MUTED, 26))
        col = QVBoxLayout()
        col.setSpacing(0)
        self.value = label("—", "stat")
        col.addWidget(self.value)
        self.caption = label(caption, "muted")
        col.addWidget(self.caption)
        lay.addLayout(col, 1)

    def set(self, value: str) -> None:
        self.value.setText(value)
