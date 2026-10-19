"""Sidebar sections other than the wizard: presets, history, logs, settings, help."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFontDatabase
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QHeaderView,
                               QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QScrollArea, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from . import theme as t
from .providers import PROVIDERS
from .store import Store
from .widgets import IconBadge, PageHeader, button, card, label


SETUP_GUIDE_URL = ("https://github.com/elevatedesigns/mail-migrator/blob/main/docs/"
                   "google-microsoft-setup.md")


def _when(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%b %d, %Y  %H:%M")
    except (TypeError, ValueError):
        return iso or ""


def _endpoint(side: dict) -> str:
    p = PROVIDERS.get(side.get("provider", "custom"), PROVIDERS["custom"])
    host = side.get("host") or p.host or "?"
    return host if p.key == "custom" else f"{p.name} ({host})"


class Section(QWidget):
    def __init__(self, icon_name: str, title: str, subtitle: str,
                 color: str = t.ACCENT) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        # Scroll instead of squeezing when the window is smaller than the content.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.margins = QVBoxLayout(holder)
        self.margins.setContentsMargins(32, 28, 32, 28)
        frame, lay = card()
        frame.setMaximumWidth(1500)
        lay.setContentsMargins(30, 28, 30, 28)
        lay.setSpacing(20)
        lay.addWidget(PageHeader(icon_name, title, subtitle, color))
        self.body = lay
        self.frame = frame
        # Centre the card, and stop it stretching across very wide screens.
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(frame, 100)
        row.addStretch(1)
        self.margins.addLayout(row, 1)
        scroll.setWidget(holder)
        outer.addWidget(scroll)

    def set_compact(self, compact: bool) -> None:
        m = 14 if compact else 32
        self.margins.setContentsMargins(m, 18 if compact else 28, m, 18 if compact else 28)
        p = 18 if compact else 30
        self.body.setContentsMargins(p, 20 if compact else 28, p, 20 if compact else 28)

    def refresh(self) -> None:
        pass


class PresetsSection(Section):
    load_requested = Signal(dict)

    def __init__(self, store: Store) -> None:
        super().__init__("bookmark", "Saved Presets",
                         "Server and option settings you saved from the Review step. "
                         "Passwords are never stored.")
        self.store = store
        self.list_box = QVBoxLayout()
        self.list_box.setSpacing(12)
        holder = QWidget()
        holder.setLayout(self.list_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)
        self.body.addWidget(scroll, 1)

    def refresh(self) -> None:
        while self.list_box.count():
            item = self.list_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        presets = self.store.list_presets()
        if not presets:
            empty = label("No presets yet. On the Review & Start step, click "
                          "“Save as Preset” to keep a migration's settings for next time.",
                          "muted", True)
            self.list_box.addWidget(empty)
        for data in presets:
            self.list_box.addWidget(self._row(data))
        self.list_box.addStretch(1)

    def _row(self, data: dict) -> QWidget:
        f = QFrame()
        f.setObjectName("inner")
        h = QHBoxLayout(f)
        h.setContentsMargins(18, 14, 14, 14)
        h.setSpacing(16)
        bulk = data.get("mode") == "bulk"
        h.addWidget(IconBadge("users" if bulk else "mail", t.PURPLE if bulk else t.ACCENT,
                              48))
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(data.get("name", "Preset"), "h3"))
        src, dst = data.get("source") or {}, data.get("destination") or {}
        col.addWidget(label(f"{_endpoint(src)}  →  {_endpoint(dst)}", "muted", True))
        col.addWidget(label(("Bulk migration" if bulk else "Single mailbox") +
                            " · saved " + _when(data.get("saved", "")), "hint"))
        h.addLayout(col, 1)
        load = button("Use preset", "arrow-right", obj="small")
        load.clicked.connect(lambda: self.load_requested.emit(data))
        delete = button("", "trash", obj="small")
        delete.setToolTip("Delete preset")
        delete.clicked.connect(lambda: (self.store.delete_preset(data["_path"]),
                                        self.refresh()))
        h.addWidget(load)
        h.addWidget(delete)
        return f


class HistorySection(Section):
    def __init__(self, store: Store) -> None:
        super().__init__("clock", "Migration History",
                         "Every simulation and migration run from this app.")
        self.store = store
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["When", "Type", "From", "To", "Result",
                                              "Messages"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(38)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        self.body.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.open_log = button("Open log", "file", obj="small")
        self.clear = button("Clear history", "trash", obj="small")
        row.addWidget(self.open_log)
        row.addStretch(1)
        row.addWidget(self.clear)
        self.body.addLayout(row)
        self.open_log.clicked.connect(self._open_log)
        self.table.doubleClicked.connect(self._open_log)
        self.clear.clicked.connect(lambda: (self.store.clear_history(), self.refresh()))
        self._items: list[dict] = []

    def refresh(self) -> None:
        self._items = self.store.history()
        self.table.setRowCount(0)
        for e in self._items:
            r = self.table.rowCount()
            self.table.insertRow(r)
            ok = e.get("ok")
            values = [_when(e.get("when", "")), e.get("mode_label", ""),
                      e.get("from", ""), e.get("to", ""), e.get("result", ""),
                      f"{e['copied']:,}" if isinstance(e.get("copied"), int) else ""]
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                item.setToolTip(v)
                if c == 4:
                    item.setForeground(QColor(t.GREEN if ok else t.RED))
                self.table.setItem(r, c, item)
        self.open_log.setEnabled(bool(self._items))

    def _open_log(self, *_):
        r = self.table.currentRow()
        if 0 <= r < len(self._items):
            logs = [p for p in self._items[r].get("logs", []) if Path(p).exists()]
            if logs:
                QDesktopServices.openUrl(QUrl.fromLocalFile(logs[0]))
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.logs_dir)))


class LogsSection(Section):
    def __init__(self, store: Store) -> None:
        super().__init__("file", "Logs",
                         "Full imapsync logs for every run, newest first.")
        self.store = store
        split = QSplitter()
        self.list = QListWidget()
        self.list.setMinimumWidth(280)
        self.view = QPlainTextEdit()
        self.view.setObjectName("log")
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.view.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        split.addWidget(self.list)
        split.addWidget(self.view)
        split.setSizes([320, 800])
        self.body.addWidget(split, 1)
        row = QHBoxLayout()
        self.open_folder = button("Open log folder", "folder-open", obj="small")
        self.open_file = button("Open in editor", "external", obj="small")
        row.addWidget(self.open_folder)
        row.addWidget(self.open_file)
        row.addStretch(1)
        self.body.addLayout(row)
        self.list.currentItemChanged.connect(self._show)
        self.open_folder.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.logs_dir))))
        self.open_file.clicked.connect(self._open_file)

    def refresh(self) -> None:
        self.list.clear()
        for p in self.store.log_files()[:500]:
            when = datetime.fromtimestamp(p.stat().st_mtime).strftime("%b %d, %Y  %H:%M")
            item = QListWidgetItem(f"{when}\n{p.stem}")
            item.setData(Qt.UserRole, str(p))
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self.view.setPlainText("No logs yet.")

    def _show(self, item, _prev=None) -> None:
        if not item:
            return
        path = Path(item.data(Qt.UserRole))
        try:
            data = path.read_bytes()[-2_000_000:]
            self.view.setPlainText(data.decode("utf-8", "replace"))
        except OSError as exc:
            self.view.setPlainText(str(exc))

    def _open_file(self) -> None:
        item = self.list.currentItem()
        if item:
            QDesktopServices.openUrl(QUrl.fromLocalFile(item.data(Qt.UserRole)))


class SettingsSection(Section):
    engine_changed = Signal(str)
    apps_changed = Signal(dict)

    def __init__(self, store: Store) -> None:
        super().__init__("settings", "Settings", "Where Mail Migrator keeps its files, and "
                                                 "which imapsync it uses.")
        self.store = store
        f, l = card("inner")
        l.addWidget(label("imapsync engine", "h3"))
        self.engine_label = label("", "muted", True)
        self.engine_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        l.addWidget(self.engine_label)
        row = QHBoxLayout()
        self.engine_path = QLineEdit()
        self.engine_path.setPlaceholderText("Automatic (bundled imapsync)")
        choose = button("Choose…", "folder-open", obj="small")
        reset = button("Use bundled", obj="small")
        row.addWidget(self.engine_path, 1)
        row.addWidget(choose)
        row.addWidget(reset)
        l.addLayout(row)
        l.addWidget(label("Only change this to use a different imapsync build.", "hint"))
        self.body.addWidget(f)

        fa, la = card("inner")
        la.addWidget(label("Google & Microsoft apps", "h3"))
        from .cloud.config import builtin_ids_present
        self.apps_note = label(
            ("This build includes Mail Migrator's own Google and Microsoft app registrations. "
             "Fill these in only to use your organization's own instead."
             if builtin_ids_present() else
             "This build has no built-in app registrations. To sign in with Google or "
             "Microsoft, enter your organization's app IDs (see the setup guide)."),
            "muted", True)
        la.addWidget(self.apps_note)
        from PySide6.QtWidgets import QGridLayout
        g = QGridLayout()
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(8)
        self.app_fields = {}
        for row, (key, text, secret, placeholder) in enumerate((
                ("google_client_id", "Google client ID", False,
                 "1234567890-abc.apps.googleusercontent.com"),
                ("google_client_secret", "Google client secret", True, "GOCSPX-…"),
                ("microsoft_client_id", "Microsoft application ID", False,
                 "00000000-0000-0000-0000-000000000000"),
                ("microsoft_tenant", "Microsoft tenant", False,
                 "common (any account) or your tenant ID / domain"))):
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            if secret:
                edit.setEchoMode(QLineEdit.Password)
            self.app_fields[key] = edit
            g.addWidget(label(text), row, 0)
            g.addWidget(edit, row, 1)
        g.setColumnStretch(1, 1)
        la.addLayout(g)
        ra = QHBoxLayout()
        save_apps = button("Save app settings", "save", obj="small")
        save_apps.clicked.connect(lambda: self.apps_changed.emit(self.app_values()))
        guide = button("Setup guide", "external", obj="small")
        guide.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(SETUP_GUIDE_URL)))
        ra.addWidget(save_apps)
        ra.addWidget(guide)
        ra.addStretch(1)
        la.addLayout(ra)
        self.body.addWidget(fa)

        f2, l2 = card("inner")
        l2.addWidget(label("Data folder", "h3"))
        l2.addWidget(label(f"Logs, presets and history are stored in:\n{store.root}",
                           "muted", True))
        open_data = button("Open data folder", "folder-open", obj="small")
        open_data.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(store.root))))
        r2 = QHBoxLayout()
        r2.addWidget(open_data)
        r2.addStretch(1)
        l2.addLayout(r2)
        self.body.addWidget(f2)
        self.body.addStretch(1)

        def pick():
            from PySide6.QtWidgets import QFileDialog
            path, _ = QFileDialog.getOpenFileName(self, "Choose imapsync")
            if path:
                self.engine_path.setText(path)
                self.engine_changed.emit(path)
        choose.clicked.connect(pick)
        reset.clicked.connect(lambda: (self.engine_path.clear(), self.engine_changed.emit("")))
        self.engine_path.editingFinished.connect(
            lambda: self.engine_changed.emit(self.engine_path.text().strip()))

    def app_values(self) -> dict:
        return {k: e.text().strip() for k, e in self.app_fields.items()}

    def set_app_values(self, values: dict) -> None:
        for k, e in self.app_fields.items():
            e.setText(values.get(k, "") or "")

    def set_engine(self, path_text: str, status: str) -> None:
        self.engine_label.setText(f"{status}\n{path_text}")


class HelpSection(Section):
    LINKS = [
        ("shield", "Google Workspace & Microsoft 365 setup",
         "Register the sign-in apps, and set up admin access for whole-organization moves.",
         SETUP_GUIDE_URL),
        ("mail", "Gmail & Google Workspace",
         "Turn on IMAP, then create an App Password (needs 2-Step Verification).",
         "https://support.google.com/accounts/answer/185833"),
        ("shield", "Microsoft 365 / Outlook.com",
         "Microsoft requires OAuth2 for IMAP. Paste an access token for the mailbox.",
         "https://imapsync.lamiral.info/FAQ.d/FAQ.Office365.txt"),
        ("key", "iCloud Mail",
         "Create an app-specific password for your Apple Account.",
         "https://support.apple.com/en-us/102654"),
        ("help", "imapsync FAQ",
         "Answers for almost every mail server, error and edge case.",
         "https://imapsync.lamiral.info/FAQ.d/"),
        ("external", "imapsync home page",
         "The engine behind Mail Migrator, by Gilles Lamiral.",
         "https://imapsync.lamiral.info/"),
    ]

    def __init__(self, version: str) -> None:
        super().__init__("help", "Help & Support",
                         "Guides for common providers and the imapsync documentation.")
        for icon_name, title, text, url in self.LINKS:
            f = QFrame()
            f.setObjectName("inner")
            h = QHBoxLayout(f)
            h.setContentsMargins(18, 14, 14, 14)
            h.setSpacing(16)
            h.addWidget(IconBadge(icon_name, t.ACCENT, 44))
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(label(title, "h3"))
            col.addWidget(label(text, "muted", True))
            h.addLayout(col, 1)
            b = button("Open", "external", obj="small")
            b.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
            h.addWidget(b)
            self.body.addWidget(f)
        self.body.addWidget(label(f"Mail Migrator {version} · imapsync is released under "
                                  "the NOLIMIT Public License.", "hint"))
        self.body.addStretch(1)
