"""Main window of Mail Migrator: sidebar, step-by-step wizard and run screen."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QSettings, QSize, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFileDialog, QHBoxLayout,
                               QInputDialog, QMainWindow, QMessageBox, QPushButton,
                               QScrollArea, QStackedWidget, QSystemTrayIcon, QVBoxLayout,
                               QWidget)

from . import __version__
from . import keepawake
from . import theme as t
from .batch import format_accounts, parse_accounts
from .engine import (Account, CommandError, Options, Server, describe_exit, find_imapsync,
                     parse_extra_args, preflight, single_side, validate)
from .cloudui import BgTask, CloudMixin
from .engine import AUTH_ADMIN, AUTH_SIGNIN
from .pages import (DataPage, MailboxesPage, OptionsPage, ReviewPage, RunPage, ServerPage,
                    SharedDrivesPage, TypePage, checked_line, kv)
from .progress import is_error_line, is_warning_line
from .providers import PROVIDERS
from .runner import AccountResult, MigrationRunner
from .sections import (HelpSection, HistorySection, LogsSection, PresetsSection,
                       SettingsSection)
from .store import Store
from .widgets import (ChecklistCard, IconBadge, InfoCard, LogCard, Stepper, button,
                      label)

APP_NAME = "Mail Migrator"
STEP_LABELS = {"type": "Migration Type", "source": "Source", "destination": "Destination",
               "data": "What to Migrate", "mailboxes": "Mailboxes", "shared": "Shared Drives",
               "options": "Options", "review": "Review & Start"}
MIN_WIDTH, MIN_HEIGHT = 860, 600
COMPACT_SIDEBAR_BELOW = 1180   # window width
CONTENT_MAX_WIDTH = 1760
KIND_NAMES = {"mail": "Email", "contacts": "Contacts", "calendars": "Calendars",
              "files": "Files", "shared": "Shared drives"}
KEY_LINES = ("success login", "failure", "Err ", "Error", "Exiting with return value")


def human_bytes(n: int) -> str:
    size = float(n)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1000 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1000
    return f"{n} bytes"


def explain(result: AccountResult, single_side_test: bool = False) -> str:
    """A short, plain-English reason for a failed run."""
    if result.error:
        return result.error
    p = result.progress
    if p and p.cert_error:
        return ("The server's security certificate could not be verified (self-signed, "
                "expired, or issued for another name). If you trust this server, untick "
                "'Verify server certificates' in Options › Advanced.")
    code = result.exit_code
    if single_side_test and code in (161, 162, 16):
        return "Login failed — check the username and password (or app password/token)."
    if single_side_test and code in (101, 102, 10):
        return "Could not connect — check the server address, security and port."
    advice = {16: " — check usernames and passwords.",
              161: " — check the source username and password.",
              162: " — check the destination username and password.",
              10: " — check the server address, security and port.",
              101: " — check the source server address, security and port.",
              102: " — check the destination server address, security and port.",
              12: " — try another Security setting."}
    return describe_exit(code if code is not None else 1) + advice.get(code, "")


def app_icon() -> QIcon:
    badge = IconBadge("mail", t.ACCENT, 512)
    badge.setAttribute(Qt.WA_TranslucentBackground)
    pm = QPixmap(512, 512)
    pm.fill(Qt.transparent)
    badge.render(pm)
    return QIcon(pm)


class CurrentPageStack(QStackedWidget):
    """A stack sized by its visible page only, so one wide page cannot widen the rest."""

    def sizeHint(self):  # noqa: N802
        w = self.currentWidget()
        return w.sizeHint() if w else super().sizeHint()

    def minimumSizeHint(self):  # noqa: N802
        w = self.currentWidget()
        return w.minimumSizeHint() if w else super().minimumSizeHint()

    # Wrapped text makes a page taller when it is narrower; pass that on to the
    # scroll area so narrow windows scroll instead of squeezing controls.
    def hasHeightForWidth(self) -> bool:  # noqa: N802
        w = self.currentWidget()
        return bool(w and w.hasHeightForWidth())

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        w = self.currentWidget()
        if w and w.hasHeightForWidth():
            return max(w.heightForWidth(width), w.minimumSizeHint().height())
        return super().heightForWidth(width)


class MainWindow(QMainWindow, CloudMixin):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.settings = QSettings()
        self.workdir = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
        os.makedirs(self.workdir, exist_ok=True)
        self.store = Store(self.workdir)
        override = self.settings.value("engine_path", "") or ""
        if override:
            os.environ["MAILMIGRATOR_IMAPSYNC"] = override
        self.engine = find_imapsync()
        self.engine_version = ""
        self.runner: MigrationRunner | None = None
        self.run_kind = ""
        self.mode = "single"
        self.steps: list[str] = []
        self.step = 0
        self.max_step = 0
        self.checks: dict[str, bool | None] = {"source": None, "destination": None}
        self.check_detail = ""
        self.last_results: list[AccountResult] = []
        self.on_run_page = False
        self._tray: QSystemTrayIcon | None = None
        self.creds: dict = {1: None, 2: None}      # signed-in / admin credentials, memory only
        self._signin_cancel: dict = {}
        self.cloud_worker = None
        self._accounts_running: list[Account] = []
        self._mail_summary = None

        self._build()
        self._load_last()
        self._set_mode(self.type_page.mode())
        self._go(0)
        self._check_engine()

    # ================================================================ layout

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._build_sidebar())
        self.sections = QStackedWidget()
        self.sections.addWidget(self._build_wizard())
        self.presets = PresetsSection(self.store)
        self.history = HistorySection(self.store)
        self.logs = LogsSection(self.store)
        self.settings_page = SettingsSection(self.store)
        self.help = HelpSection(__version__)
        for w in (self.presets, self.history, self.logs, self.settings_page, self.help):
            self.sections.addWidget(w)
        h.addWidget(self.sections, 1)
        self.setCentralWidget(root)

        self.presets.load_requested.connect(self._apply_preset)
        self.settings_page.engine_changed.connect(self._engine_changed)
        self.settings_page.apps_changed.connect(self._apps_changed)
        self.settings_page.set_app_values(self._app_overrides())

        # Fit the screen we open on; small laptop screens included.
        screen = QApplication.primaryScreen().availableGeometry()
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)
        self.resize(max(MIN_WIDTH, min(1480, int(screen.width() * 0.92))),
                    max(MIN_HEIGHT, min(980, int(screen.height() * 0.92))))

    def _build_sidebar(self) -> QWidget:
        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(270)
        self.sidebar = side
        v = QVBoxLayout(side)
        self.sidebar_layout = v
        v.setContentsMargins(16, 26, 16, 22)
        v.setSpacing(6)
        brand = QHBoxLayout()
        brand.setContentsMargins(8, 0, 0, 0)
        brand.setSpacing(14)
        brand.addWidget(IconBadge("mail", t.ACCENT, 56))
        col = QVBoxLayout()
        col.setSpacing(0)
        self.brand_labels = [label(APP_NAME, "appName"), label(f"v{__version__}", "appVersion")]
        for lb in self.brand_labels:
            col.addWidget(lb)
        brand.addLayout(col, 1)
        v.addLayout(brand)
        v.addSpacing(30)
        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        items = [("send", "New Migration"), ("bookmark", "Saved Presets"),
                 ("clock", "Migration History"), ("file", "Logs"), ("settings", "Settings")]
        for i, (icon_name, text) in enumerate(items):
            b = self._nav_button(icon_name, text, i)
            v.addWidget(b)
        v.addStretch(1)
        v.addWidget(self._nav_button("help", "Help && Support", 5))
        self.nav.button(0).setChecked(True)
        self.nav.idClicked.connect(self._show_section)
        return side

    def _nav_button(self, icon_name: str, text: str, index: int) -> QPushButton:
        b = QPushButton("  " + text)
        b.setProperty("full_text", "  " + text)
        b.setToolTip(text.replace("&&", "&"))
        b.setObjectName("nav")
        b.setCheckable(True)
        b.setIcon(t.icon(icon_name, t.MUTED, 22))
        b.setIconSize(QSize(22, 22))
        b.setCursor(Qt.PointingHandCursor)
        self.nav.addButton(b, index)
        return b

    def _build_wizard(self) -> QWidget:
        w = QWidget()
        w.setMaximumWidth(CONTENT_MAX_WIDTH)
        v = QVBoxLayout(w)
        self.wizard_layout = v
        v.setContentsMargins(30, 18, 30, 22)
        v.setSpacing(14)
        self.stepper = Stepper()
        self.stepper.step_clicked.connect(self._stepper_clicked)
        v.addWidget(self.stepper)

        content = QHBoxLayout()
        content.setSpacing(22)
        self.pages = CurrentPageStack()
        self.pages.currentChanged.connect(self._page_changed)
        self.type_page = TypePage()
        self.src_page = ServerPage(1)
        self.dst_page = ServerPage(2)
        self.data_page = DataPage()
        self.shared_page = SharedDrivesPage()
        self.mbx_page = MailboxesPage()
        self.opt_page = OptionsPage()
        self.review_page = ReviewPage()
        self.run_page = RunPage()
        self.page_for = {"type": self.type_page, "source": self.src_page,
                         "destination": self.dst_page, "data": self.data_page,
                         "mailboxes": self.mbx_page, "shared": self.shared_page,
                         "options": self.opt_page, "review": self.review_page}
        for p in (self.type_page, self.src_page, self.dst_page, self.data_page, self.mbx_page,
                  self.shared_page, self.opt_page, self.review_page, self.run_page):
            self.pages.addWidget(p)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self.pages)
        self.page_scroll = scroll
        content.addWidget(scroll, 1)

        right = QVBoxLayout()
        right.setSpacing(18)
        self.info_card = InfoCard()
        self.tips_card = ChecklistCard()
        self.log_card = LogCard()
        right.addWidget(self.info_card)
        right.addWidget(self.tips_card)
        right.addWidget(self.log_card, 1)
        right_w = QWidget()
        right_w.setLayout(right)
        right.setContentsMargins(0, 0, 0, 0)
        self.log_card.setMinimumHeight(240)
        # The right column scrolls on short windows rather than squeezing its cards.
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_scroll.setWidget(right_w)
        right_scroll.setFixedWidth(450)
        self.right_column = right_scroll
        content.addWidget(right_scroll)
        v.addLayout(content, 1)

        footer = QHBoxLayout()
        footer.setSpacing(12)
        self.back_btn = button("Back", "arrow-left", "ghost")
        self.log_btn = button("Log", "file", "ghost")
        self.log_btn.setCheckable(True)
        self.log_btn.setToolTip("Show the migration log and tips")
        self.log_btn.toggled.connect(lambda _on: self._apply_responsive())
        self.cancel_btn = button("Cancel", obj="ghost")
        self.save_btn = button("Save as Preset", "save", "ghost")
        self.sim_btn = button("Simulate", "play", "ghost")
        self.stop_btn = button("Stop", "stop", "danger", "#fecaca")
        self.logs_btn = button("Open log folder", "folder-open", "ghost")
        self.next_btn = button("Continue", "arrow-right", "primary", "#ffffff", True)
        self.start_btn = button("Start Migration", "play", "go", "#ffffff")
        self.new_btn = button("New Migration", "plus", "primary", "#ffffff")
        footer.addWidget(self.back_btn)
        footer.addWidget(self.cancel_btn)
        footer.addWidget(self.log_btn)
        footer.addStretch(1)
        for b in (self.save_btn, self.sim_btn, self.logs_btn, self.stop_btn, self.next_btn,
                  self.start_btn, self.new_btn):
            footer.addWidget(b)
        v.addLayout(footer)

        self.back_btn.clicked.connect(self._back)
        self.cancel_btn.clicked.connect(self._cancel)
        self.next_btn.clicked.connect(self._next)
        self.save_btn.clicked.connect(self._save_preset)
        self.sim_btn.clicked.connect(lambda: self._start_run("dry"))
        self.start_btn.clicked.connect(self._start_clicked)
        self.stop_btn.clicked.connect(self._stop)
        self.logs_btn.clicked.connect(self._open_logs)
        self.new_btn.clicked.connect(self._new_migration)

        self.type_page.changed.connect(self._set_mode)
        self.src_page.test_requested.connect(lambda: self._test_side(1))
        self.dst_page.test_requested.connect(lambda: self._test_side(2))
        self.mbx_page.import_requested.connect(self._import_csv)
        self.mbx_page.export_requested.connect(self._export_csv)
        self.mbx_page.template_requested.connect(self._save_template)
        self.mbx_page.directory_requested.connect(self._load_directory)
        self.shared_page.load_requested.connect(self._load_shared)
        self.data_page.changed.connect(self._update_steps)
        for n, page in ((1, self.src_page), (2, self.dst_page)):
            page.signin_requested.connect(lambda n=n: self._sign_in(n))
            page.admin_requested.connect(lambda n=n: self._admin_connect(n))
            for combo in (page.provider, page.auth):
                combo.currentIndexChanged.connect(self._update_availability)
            for edit in (page.carddav, page.caldav, page.webdav):
                edit.textChanged.connect(self._update_availability)
        self.review_page.estimate_requested.connect(lambda: self._start_run("sizes"))
        self.review_page.edit_requested.connect(self._edit)
        # Centre the wizard and stop it stretching across very wide screens.
        outer = QWidget()
        hl = QHBoxLayout(outer)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addStretch(1)
        hl.addWidget(w, 100)
        hl.addStretch(1)
        return outer

    # ============================================================ layout

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_responsive()

    def _apply_responsive(self) -> None:
        """Adapt the layout to the window size (laptops up to large monitors)."""
        if not hasattr(self, "help"):   # still being built
            return
        width = self.width()
        compact = width < COMPACT_SIDEBAR_BELOW
        self.sidebar.setFixedWidth(84 if compact else 270)
        self.sidebar_layout.setContentsMargins(12 if compact else 16, 26, 12 if compact else 16, 22)
        for lb in self.brand_labels:
            lb.setVisible(not compact)
        for b in self.nav.buttons():
            b.setText("" if compact else b.property("full_text"))
        available = min(width - self.sidebar.width(), CONTENT_MAX_WIDTH)
        narrow = available < 900
        m = 16 if narrow else 30
        self.wizard_layout.setContentsMargins(m, 12 if narrow else 18, m, 14 if narrow else 22)
        for section in (self.presets, self.history, self.logs, self.settings_page, self.help):
            section.set_compact(narrow)
        # Secondary footer buttons become icon-only when space is tight.
        self.save_btn.setText("" if narrow else "Save as Preset")
        self.logs_btn.setText("" if narrow else "Open log folder")
        self.save_btn.setToolTip("Save as Preset")
        self.logs_btn.setToolTip("Open log folder")
        if available >= 1250:
            column = 450
        elif available >= 1020:
            column = 360
        else:
            column = 0          # no room: the Log button swaps it in instead
        on_type_step = (not self.on_run_page and self.steps and
                        self.steps[self.step] == "type")
        self.log_btn.setVisible(column == 0 and not on_type_step)
        if column:
            self.right_column.setFixedWidth(column)
            self.right_column.setVisible(not on_type_step)
            self.page_scroll.setVisible(True)
        else:
            show_log = self.log_btn.isChecked() and not on_type_step
            self.right_column.setMaximumWidth(16777215)
            self.right_column.setMinimumWidth(0)
            self.right_column.setVisible(show_log)
            self.page_scroll.setVisible(not show_log)
            self.log_btn.setText("Back to page" if show_log else "Log")

    # ============================================================ navigation

    def _show_section(self, index: int) -> None:
        self.sections.setCurrentIndex(index)
        widget = self.sections.currentWidget()
        if hasattr(widget, "refresh"):
            widget.refresh()

    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        self.src_page.set_bulk(mode == "bulk")
        self.dst_page.set_bulk(mode == "bulk")
        self._update_steps()
        self._reset_checks()
        self._update_availability()

    def _update_steps(self) -> None:
        current = self.steps[self.step] if self.steps else "type"
        kinds = self.data_page.selected()
        steps = ["type", "source", "destination", "data"]
        if self.mode == "bulk":
            steps.append("mailboxes")
        if "shared" in kinds:
            steps.append("shared")
        steps += ["options", "review"]
        self.steps = steps
        self.stepper.set_steps([STEP_LABELS[s] for s in steps])
        self.step = steps.index(current) if current in steps else min(self.step, len(steps) - 1)
        self.max_step = min(self.max_step, len(steps) - 1)
        self.stepper.set_current(self.step, self.max_step - 1 if self.max_step > self.step
                                 else self.step - 1)

    def _go(self, index: int) -> None:
        self.on_run_page = False
        if hasattr(self, "log_btn") and self.log_btn.isChecked():
            self.log_btn.blockSignals(True)
            self.log_btn.setChecked(False)   # a new step always shows the page
            self.log_btn.blockSignals(False)
        self.step = max(0, min(index, len(self.steps) - 1))
        self.max_step = max(self.max_step, self.step)
        key = self.steps[self.step]
        page = self.page_for[key]
        self.pages.setCurrentWidget(page)
        self.page_scroll.verticalScrollBar().setValue(0)
        self.stepper.set_current(self.step, self.max_step - 1 if self.max_step > self.step
                                 else self.step - 1)
        if key == "review":
            self._fill_review()
        if key == "data":
            self._update_availability()
        if key == "options":
            self.opt_page.set_kinds(self.data_page.selected(), self.dst_page.platform())
        self._update_side_cards()
        self._update_footer()

    def _page_changed(self, _index: int) -> None:
        from PySide6.QtWidgets import QSizePolicy
        for i in range(self.pages.count()):
            page = self.pages.widget(i)
            policy = (QSizePolicy.Preferred if page is self.pages.currentWidget()
                      else QSizePolicy.Ignored)
            page.setSizePolicy(policy, policy)
        self.pages.updateGeometry()

    def _stepper_clicked(self, index: int) -> None:
        if self.on_run_page or self._busy():
            return
        if index <= self.max_step:
            self._go(index)

    def _edit(self, key: str) -> None:
        if key in self.steps:
            self._go(self.steps.index(key))

    def _back(self) -> None:
        if self.on_run_page:
            self._go(len(self.steps) - 1)
        else:
            self._go(self.step - 1)

    def _cancel(self) -> None:
        if QMessageBox.question(self, APP_NAME, "Start over? Your settings stay filled in, "
                                "but connection checks are reset.") == QMessageBox.Yes:
            self._reset_checks()
            self.max_step = 0
            self._go(0)

    def _next(self) -> None:
        problem = self._validate_step(self.steps[self.step])
        if problem:
            QMessageBox.warning(self, APP_NAME, problem)
            return
        self._go(self.step + 1)

    def _validate_step(self, key: str) -> str:
        if key in ("source", "destination"):
            page = self.src_page if key == "source" else self.dst_page
            word = "Source" if key == "source" else "Destination"
            s = page.server()
            p = PROVIDERS[s.provider]
            if (not p.flag or p.flag == "exchange") and not s.host:
                return f"Enter the {word.lower()} server address."
            n = 1 if key == "source" else 2
            mode = page.auth_mode()
            cloud = page.platform() != "imap"
            if cloud and mode == AUTH_SIGNIN:
                if self.mode == "bulk":
                    return (f"{word}: a bulk migration needs admin access on this side, or app "
                            "passwords / tokens (email only).")
                if self.creds.get(n) is None or self.creds[n].admin:
                    return f"{word}: click “Sign in” first."
                return ""
            if cloud and mode == AUTH_ADMIN:
                if self.creds.get(n) is None or not self.creds[n].admin:
                    return f"{word}: connect with admin access first."
                if self.mode == "single" and not page.user.text().strip():
                    return f"{word}: enter the mailbox to migrate."
                return ""
            if self.mode == "single":
                if not page.user.text().strip():
                    return f"Enter the {word.lower()} username."
                if not page.secret.text():
                    return f"Enter the {word.lower()} password or token."
        if key == "data" and not self.data_page.selected():
            return "Choose at least one kind of data to migrate."
        if key == "shared" and "shared" in self.data_page.selected() and \
                not self.shared_page.plans():
            return "Load the shared drives and tick at least one, or untick Shared drives."
        if key == "mailboxes":
            accounts = self._accounts()
            if not accounts:
                return "Add at least one mailbox."
            for i, a in enumerate(accounts, 1):
                try:
                    validate(self.src_page.server(), self.dst_page.server(), a)
                except CommandError as exc:
                    return f"Mailbox {i}: {exc}"
        if key == "options":
            try:
                parse_extra_args(self.opt_page.options().extra_args)
            except CommandError as exc:
                return str(exc)
        return ""

    def _update_footer(self) -> None:
        busy = self._busy()
        keepawake.set_active(busy)
        key = self.steps[self.step] if not self.on_run_page else "run"
        first = key == "type"
        review = key == "review"
        run = key == "run"
        self.cancel_btn.setVisible(first)
        self.back_btn.setVisible(not first)
        self.back_btn.setEnabled(not (run and busy))
        self.back_btn.setText("Back to review" if run else "Back")
        self.next_btn.setVisible(not review and not run)
        self.save_btn.setVisible(review)
        self.sim_btn.setVisible(review)
        self.start_btn.setVisible(review)
        for b in (self.save_btn, self.sim_btn, self.start_btn, self.next_btn):
            b.setEnabled(not busy and bool(self.engine))
        self.next_btn.setEnabled(True)
        self.stop_btn.setVisible(run and busy)
        self.logs_btn.setVisible(run and not busy)
        self.new_btn.setVisible(run and not busy)

    def _update_side_cards(self) -> None:
        if self.on_run_page:
            page = self.run_page
            self.info_card.set("Running", page.info[0], page.info[1])
            self._tips_from_checks()
            self._apply_responsive()
            return
        key = self.steps[self.step]
        page = self.page_for[key]
        n = len(self.steps)
        # The first step uses the full width, like the welcome screen in the design.
        self._apply_responsive()
        self.info_card.set(f"Step {self.step + 1} of {n}", page.info[0], page.info[1])
        if key == "review":
            self._tips_from_checks()
        else:
            if hasattr(self, "_checks_btn"):
                self._checks_btn.setVisible(False)
            self.tips_card.set(page.tips_title, [(tip, "info") for tip in page.tips])

    def _tips_from_checks(self) -> None:
        src, dst = self.checks["source"], self.checks["destination"]

        def state(v):
            return "ok" if v else ("fail" if v is False else "pending")
        valid = not self._settings_problem()
        items = [
            ("Source server connection tested successfully" if src else
             ("Source connection failed" if src is False else "Source connection not tested yet"),
             state(src)),
            ("Destination server connection tested successfully" if dst else
             ("Destination connection failed" if dst is False else
              "Destination connection not tested yet"), state(dst)),
            ("Authentication verified for " + ("every mailbox" if self.mode == "bulk"
                                               else "both accounts"),
             state(src and dst if (src is not None and dst is not None) else None)),
            ("Migration settings are valid" if valid else self._settings_problem(),
             "ok" if valid else "fail"),
        ]
        ready = bool(src and dst and valid)
        self.tips_card.set("Ready to migrate" if ready else "Before you start", items,
                           "check" if ready else "shield",
                           t.GREEN_DARK if ready else t.AMBER,
                           "Both servers are connected and ready to go." if ready else
                           (self.check_detail or "Click Run checks to sign in to both servers."))
        if not self.on_run_page and self.steps[self.step] == "review" and not ready:
            if not hasattr(self, "_checks_btn"):
                self._checks_btn = button("Run checks", "refresh", obj="small")
                self._checks_btn.clicked.connect(lambda: self._start_run("test"))
            self.tips_card.items_box.addWidget(self._checks_btn, 0, Qt.AlignLeft)
            self._checks_btn.setVisible(True)
            self._checks_btn.setEnabled(not self._busy() and bool(self.engine))
            self.tips_card.subtitle.setText(self.check_detail or
                                            "Sign in to both servers before migrating.")
        elif hasattr(self, "_checks_btn"):
            self._checks_btn.setVisible(False)

    def _settings_problem(self) -> str:
        for key in self.steps:
            if key in ("type", "review"):
                continue
            problem = self._validate_step(key)
            if problem:
                return problem
        return ""

    # ================================================================== data

    def _accounts(self) -> list[Account]:
        """Mailbox pairs, with token providers in place of passwords on signed-in sides."""
        if self.mode == "bulk":
            rows = self.mbx_page.table.accounts()
        else:
            users = []
            for n, page in ((1, self.src_page), (2, self.dst_page)):
                cred = self.creds.get(n)
                if page.platform() != "imap" and page.auth_mode() == AUTH_SIGNIN and cred:
                    users.append(cred.email)
                else:
                    users.append(page.user.text().strip())
            rows = [Account(users[0], self.src_page.secret.text(), users[1],
                            self.dst_page.secret.text())]
        out = []
        for a in rows:
            s1, s2 = a.secret1, a.secret2
            if self.src_page.uses_cloud_login():
                s1 = self._token_provider(1, a.user1)
            if self.dst_page.uses_cloud_login():
                s2 = self._token_provider(2, a.user2)
            out.append(Account(a.user1, s1, a.user2, s2))
        return out

    def _profile(self) -> dict:
        src = self.src_page.server().to_dict()
        dst = self.dst_page.server().to_dict()
        if self.mode == "single":
            src["user"] = self.src_page.user.text().strip()
            dst["user"] = self.dst_page.user.text().strip()
        src.update(self.src_page.admin_settings(), **self.src_page.dav())
        dst.update(self.dst_page.admin_settings())
        data = {"version": 3, "mode": self.mode, "source": src, "destination": dst,
                "kinds": self.data_page.selected(),
                "options": self.opt_page.options().to_dict(),
                "extras": self.opt_page.extras()}
        if self.mode == "bulk":
            data["accounts"] = [{"user1": a.user1, "user2": a.user2}
                                for a in self.mbx_page.table.accounts()]
        return data

    def _apply_profile(self, data: dict) -> None:
        self.type_page.set_mode(data.get("mode") if data.get("mode") in ("single", "bulk")
                                else "single")
        src, dst = data.get("source") or {}, data.get("destination") or {}
        self.src_page.set_server(Server.from_dict(src))
        self.dst_page.set_server(Server.from_dict(dst))
        self.src_page.user.setText(str(src.get("user", "")))
        self.dst_page.user.setText(str(dst.get("user", "")))
        self.src_page.set_admin_settings(src)
        self.dst_page.set_admin_settings(dst)
        for key, edit in (("carddav", self.src_page.carddav), ("caldav", self.src_page.caldav),
                          ("webdav", self.src_page.webdav)):
            if src.get(key):
                edit.setText(str(src[key]))
        if isinstance(data.get("kinds"), list):
            self._update_availability()
            self.data_page.set_selected([str(k) for k in data["kinds"]])
        self.opt_page.set_options(Options.from_dict(data.get("options") or {}),
                                  data.get("extras") or {})
        accounts = [Account(str(a.get("user1", "")), "", str(a.get("user2", "")), "")
                    for a in data.get("accounts") or [] if isinstance(a, dict)]
        if accounts:
            self.mbx_page.set_accounts(accounts)

    def _load_last(self) -> None:
        raw = self.settings.value("last_profile", "")
        if raw:
            try:
                self._apply_profile(json.loads(raw))
            except (ValueError, TypeError, AttributeError):
                pass

    def _apply_preset(self, data: dict) -> None:
        self._apply_profile(data)
        self._reset_checks()
        self.nav.button(0).setChecked(True)
        self._show_section(0)
        self.max_step = 0
        self._go(1)
        self.log_card.add(f"Loaded preset “{data.get('name', '')}”. Enter the passwords "
                          "to continue.", "heading")

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Save as Preset", "Preset name:",
                                        text=self._default_preset_name())
        if ok and name.strip():
            self.store.save_preset(name.strip(), self._profile())
            self.log_card.add(f"Saved preset “{name.strip()}” (without passwords).", "ok")

    def _default_preset_name(self) -> str:
        s, d = self.src_page.server(), self.dst_page.server()
        return f"{s.host or PROVIDERS[s.provider].name} → {d.host or PROVIDERS[d.provider].name}"

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._busy():
            if QMessageBox.question(self, APP_NAME,
                                    "A migration is running. Stop it and quit?") != QMessageBox.Yes:
                event.ignore()
                return
            if self.runner:
                self.runner.stop()
            if self.cloud_worker and hasattr(self.cloud_worker, "stop"):
                self.cloud_worker.stop()
                self.cloud_worker.wait(5000)
        self.settings.setValue("last_profile", json.dumps(self._profile()))
        super().closeEvent(event)

    # ================================================================ engine

    def _check_engine(self) -> None:
        self.log_card.add(f"{APP_NAME} v{__version__} ready")
        if not self.engine:
            msg = ("imapsync was not found. Install it, or choose it in Settings.")
            self.log_card.add(msg, "error")
            self.settings_page.set_engine("", "Not found")
            self._update_footer()
            return
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.setWorkingDirectory(self.workdir)

        def done(code, _status):
            try:
                out = bytes(proc.readAll()).decode("utf-8", "replace").strip()
            except RuntimeError:  # window closed while checking
                return
            version = out.splitlines()[-1] if out else ""
            if code == 0 and version:
                self.engine_version = version
                self.log_card.add(f"imapsync {version} ready", "ok")
                self.settings_page.set_engine(" ".join(self.engine), f"imapsync {version}")
            else:
                self.log_card.add("imapsync could not start: " + (out[-400:] or "no output"),
                                  "error")
                self.settings_page.set_engine(" ".join(self.engine), "Could not start")
            proc.deleteLater()

        proc.finished.connect(done)
        proc.start(self.engine[0], self.engine[1:] + ["--version"])
        self.log_card.add("Waiting for your settings…")

    def _engine_changed(self, path: str) -> None:
        self.settings.setValue("engine_path", path)
        if path:
            os.environ["MAILMIGRATOR_IMAPSYNC"] = path
        else:
            os.environ.pop("MAILMIGRATOR_IMAPSYNC", None)
        self.engine = find_imapsync()
        self._check_engine()

    # ================================================================== runs

    def _busy(self) -> bool:
        return bool((self.runner and self.runner.is_running()) or self.cloud_worker)

    def _reset_checks(self) -> None:
        self.checks = {"source": None, "destination": None}
        self.check_detail = ""

    def _launch(self, kind: str, src: Server, dst: Server, accounts: list[Account],
                options: Options) -> None:
        self.run_kind = kind
        self.runner = MigrationRunner(self.engine, self.workdir, self)
        self.runner.line.connect(self._on_line)
        self.runner.progress.connect(self._on_progress)
        self.runner.account_started.connect(self._on_account_started)
        self.runner.account_finished.connect(self._on_account_finished)
        self.runner.finished.connect(self._on_finished)
        self._accounts_running = accounts
        self.runner.start(src, dst, accounts, options)
        self._update_footer()
        if not self.on_run_page and self.steps[self.step] == "review":
            self._tips_from_checks()

    def _test_side(self, side: int) -> None:
        if self._busy() or not self.engine:
            return
        page = self.src_page if side == 1 else self.dst_page
        key = "source" if side == 1 else "destination"
        problem = self._validate_step(key)
        if problem:
            page.set_test_result(False, problem)
            return
        user = page.user.text().strip()
        secret = page.secret.text()
        page.set_testing(True)
        self.log_card.add(f"Testing {key} server connection…", "heading")
        if page.uses_cloud_login():
            cred = self.creds.get(side)
            user = cred.email if page.auth_mode() == AUTH_SIGNIN else user
            if not user:
                page.set_testing(False)
                page.set_test_result(False, "Enter the mailbox to test.")
                return
            kinds = self.data_page.selected()
            target = self._side(side)

            def api_done(desc, error):
                if error is not None:
                    page.set_testing(False)
                    page.set_test_result(False, f"Access check failed: {error}")
                    self.checks[key] = False
                    self.log_card.add(f"{key.capitalize()} access check failed: {error}",
                                      "error")
                    return
                self.log_card.add(f"{key.capitalize()} access OK: {desc}", "ok")
                if "mail" not in kinds:
                    page.set_testing(False)
                    page.set_test_result(True, f"Connection successful — {desc}.")
                    self.checks[key] = True
                    return
                self._run_single_side(side, user, self._token_provider(side, user))

            from .cloud.services import check_access
            BgTask(lambda: check_access(target, user, kinds), api_done, self)
            return
        self._run_single_side(side, user, secret)

    def _run_single_side(self, side: int, user: str, secret) -> None:
        page = self.src_page if side == 1 else self.dst_page
        src, dst, account = single_side(page.server(), user, secret)
        options = Options(just_login=True, verify_certs=self.opt_page.verify.isChecked())
        self._launch(f"test{side}", src, dst, [account], options)

    def _start_clicked(self) -> None:
        if self.opt_page.simulate_first.isChecked():
            self._start_run("dry", then_migrate=True)
        else:
            self._start_run("migrate")

    def _start_run(self, kind: str, then_migrate: bool = False) -> None:
        if self._busy() or not self.engine:
            return
        accounts = self._accounts()
        src, dst = self.src_page.server(), self.dst_page.server()
        options = self.opt_page.options()
        problem = self._settings_problem()
        if problem:
            QMessageBox.warning(self, APP_NAME, problem)
            return
        for i, a in enumerate(accounts, 1):
            try:
                preflight(src, dst, a, options)
            except CommandError as exc:
                QMessageBox.warning(self, APP_NAME,
                                    (f"Mailbox {i}: " if len(accounts) > 1 else "") + str(exc))
                return
        kinds = self.data_page.selected()
        mail = "mail" in kinds
        if kind == "migrate" and mail and not self._confirm_deletions(options):
            return
        self._then_migrate = then_migrate
        self._accounts_running = accounts
        self._mail_summary = None
        self._cloud_stats = []
        self.last_results = []
        if kind == "test":
            options = Options(just_login=True, verify_certs=options.verify_certs)
            self.log_card.add("Checking both servers for "
                              f"{len(accounts)} mailbox{'es' if len(accounts) > 1 else ''}…",
                              "heading")
            if not mail:
                self.run_kind = "test"
                self._finish_checks([], True)
                return
        elif kind == "sizes" and not mail:
            QMessageBox.information(self, APP_NAME, "Estimate counts email. Use Simulate to "
                                                    "see what would be copied for contacts, "
                                                    "calendars and files.")
            return
        elif kind == "sizes":
            options.just_sizes = True
            self.review_page.estimate_btn.setEnabled(False)
            self.review_page.set_estimate(None, None, "", "Counting messages on the source…")
            self.log_card.add("Estimating migration size…", "heading")
        else:
            options.dry_run = kind == "dry"
            self._show_run_page(kind, accounts)
            if not mail:
                self.run_kind = kind
                if not self._start_cloud_phase(kind):
                    self._finalize_run(kind, False)
                return
        self._launch(kind, src, dst, accounts, options)

    def _confirm_deletions(self, options: Options) -> bool:
        if options.delete1 and QMessageBox.warning(
                self, APP_NAME, "Messages will be DELETED from the source mailbox after they "
                "are copied. This cannot be undone. Continue?",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return False
        if options.delete2 and QMessageBox.warning(
                self, APP_NAME, "Messages on the destination that are not on the source "
                "will be DELETED from the destination. Continue?",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return False
        return True

    def _show_run_page(self, kind: str, accounts: list[Account]) -> None:
        self.on_run_page = True
        kinds = self.data_page.selected()
        plans = self.shared_page.plans() if "shared" in kinds else []
        self.run_page.reset(kind, accounts, kinds, plans)
        self.pages.setCurrentWidget(self.run_page)
        self.stepper.set_current(len(self.steps), len(self.steps) - 1)
        self.log_card.add("Simulation started (nothing will be changed)" if kind == "dry"
                          else "Migration started", "heading")
        self._update_side_cards()

    def _stop(self) -> None:
        self.stop_btn.setEnabled(False)
        self._then_migrate = False
        self.run_page.status.setText("Stopping…")
        self._stopping = True
        if self.runner and self.runner.is_running():
            self.runner.stop()
        if self.cloud_worker:
            self.cloud_worker.stop()

    # -- runner signals --

    def _on_line(self, line: str) -> None:
        if not line.strip():
            return
        kind = "error" if is_error_line(line) else ("warning" if is_warning_line(line) else "")
        if self.run_kind in ("dry", "migrate") or any(k in line for k in KEY_LINES):
            self.log_card.add(line, kind)

    def _on_account_started(self, index: int, _command: str) -> None:
        a = self._accounts_running[index]
        if self.run_kind in ("dry", "migrate"):
            self.run_page.set_row(index, "Running…", t.AMBER)
            n = len(self._accounts_running)
            self.run_page.status.setText(f"{a.user1} → {a.user2}" if n == 1 else
                                         f"Mailbox {index + 1} of {n}: {a.user1} → {a.user2}")
            self.run_page.bar.setRange(0, 0)
        elif self.run_kind == "test" and self.mode == "bulk":
            self.mbx_page.table.set_status(index, "Checking…", t.AMBER)

    def _on_progress(self, index: int, p) -> None:
        if self.run_kind not in ("dry", "migrate"):
            return
        n = len(self._accounts_running)
        bits = []
        if p.folder_count:
            bits.append(f"Folder {p.folder_index} of {p.folder_count}: {p.folder}")
        if p.total:
            bits.append(f"{p.done:,} of {p.total:,} messages checked")
        self.run_page.detail.setText("  ·  ".join(bits))
        frac = p.fraction
        if frac is not None:
            if self.run_page.bar.maximum() == 0:
                self.run_page.bar.setRange(0, 1000)
            overall = (index + frac) / max(1, n)
            self.run_page.bar.setValue(int(overall * 1000))
            self.run_page.percent.setText(f"{overall * 100:.0f}% complete")

    def _on_account_finished(self, index: int, result: AccountResult) -> None:
        ok = result.exit_code == 0 and not result.error
        kind = self.run_kind
        if kind in ("dry", "migrate"):
            p = result.progress
            if ok:
                text = "Done"
                if p and p.transferred is not None:
                    text += f" — {p.transferred:,} " + ("would be copied" if kind == "dry"
                                                       else "copied")
                self.run_page.set_row(index, text, t.GREEN)
            else:
                self.run_page.set_row(index, explain(result), t.RED)
        elif kind == "test" and self.mode == "bulk":
            self.mbx_page.table.set_status(index, "OK" if ok else explain(result),
                                           t.GREEN if ok else t.RED)

    def _on_finished(self, results: list[AccountResult]) -> None:
        kind = self.run_kind
        if kind in ("dry", "migrate"):
            self.last_results = results
        ran = [r for r in results if r.exit_code is not None or r.error]
        ok = [r for r in ran if r.exit_code == 0 and not r.error]
        all_ok = bool(ran) and len(ok) == len(results)
        stopped = any(r.exit_code == 6 for r in results)

        if kind in ("test1", "test2"):
            page = self.src_page if kind == "test1" else self.dst_page
            key = "source" if kind == "test1" else "destination"
            page.set_testing(False)
            msg = ("Connection successful — signed in to the "
                   f"{key} mailbox." if all_ok else explain(results[0], True))
            page.set_test_result(all_ok, msg)
            self.checks[key] = all_ok
            self.log_card.add(msg, "ok" if all_ok else "error")
        elif kind == "test":
            self._finish_checks(results, all_ok)
            return
        elif kind == "sizes":
            self.review_page.estimate_btn.setEnabled(True)
            msgs = sum(r.progress.sizes[1].get("messages", 0) for r in ok if r.progress)
            folders = sum(r.progress.sizes[1].get("folders", 0) for r in ok if r.progress)
            size = sum(r.progress.sizes[1].get("bytes", 0) for r in ok if r.progress)
            if ok:
                note = ("Counted on the source server" + (f" across {len(ok)} mailboxes"
                                                          if len(ok) > 1 else "") +
                        ", using your folder and date filters.")
                if len(ok) < len(results):
                    note += f" {len(results) - len(ok)} mailbox(es) could not be counted."
                self.review_page.set_estimate(msgs, folders, human_bytes(size), note)
                self.log_card.add(f"Estimate: {msgs:,} messages in {folders} folders "
                                  f"({human_bytes(size)})", "ok")
                # A successful count proves both logins work too.
                if all_ok:
                    self.checks["source"] = self.checks["destination"] = True
            else:
                self.review_page.set_estimate(None, None, "", "Could not count: " +
                                              explain(results[0]))
                self.log_card.add("Estimate failed: " + explain(results[0]), "error")
        else:
            self._finish_run(kind, results, ok, all_ok, stopped)

        self._update_footer()
        if not self.on_run_page and self.steps[self.step] == "review":
            self._tips_from_checks()
        elif self.on_run_page:
            self._tips_from_checks()

    def _finish_checks(self, results: list[AccountResult], mail_ok: bool) -> None:
        """After the email logins (if any), check contacts/calendars/files access."""
        self.checks["source"] = self.checks["destination"] = mail_ok
        detail = ""
        if not mail_ok:
            bad = [r for r in results if not (r.exit_code == 0 and not r.error)]
            codes = {r.exit_code for r in bad}
            if codes <= {162, 102}:
                self.checks["source"] = True
            if codes <= {161, 101}:
                self.checks["destination"] = True
            detail = (explain(bad[0]) if len(results) == 1 else
                      f"{len(bad)} of {len(results)} mailboxes failed. "
                      "See the Mailboxes step for details.")
        self.cloud_worker = True  # busy marker while API checks run

        def api_done(problems):
            self.cloud_worker = None
            nonlocal detail
            for n, account, message in problems:
                self.checks["source" if n == 1 else "destination"] = False
                who = account.user1 if n == 1 else account.user2
                self.log_card.add(f"{'Source' if n == 1 else 'Destination'} {who}: {message}",
                                  "error")
                detail = detail or f"{who}: {message}"
            ok = self.checks["source"] and self.checks["destination"]
            self.check_detail = "" if ok else detail
            self.log_card.add("All connection checks passed" if ok else
                              "Connection checks failed: " + self.check_detail,
                              "ok" if ok else "error")
            self._update_footer()
            if not self.on_run_page and self.steps[self.step] == "review":
                self._tips_from_checks()

        self._api_checks(self._accounts_running, api_done)
        self._update_footer()

    def _finish_run(self, kind, results, ok, all_ok, stopped) -> None:
        """Email finished: remember its numbers, then run the other data types."""
        self._mail_summary = {
            "copied": sum(r.progress.transferred or 0 for r in results if r.progress),
            "skipped": sum(r.progress.skipped or 0 for r in results if r.progress),
            "errors": sum(r.progress.errors or 0 for r in results if r.progress),
            "bytes": [r.progress.bytes_text for r in results
                      if r.progress and r.progress.bytes_text],
            "ok": all_ok, "n_ok": len(ok), "n": len(results),
            "title": explain(results[0]) if len(results) == 1 and not all_ok else "",
        }
        if not stopped and self._start_cloud_phase(kind):
            return
        self._finalize_run(kind, stopped)

    def _finalize_run(self, kind: str, stopped: bool) -> None:
        rp = self.run_page
        rp.bar.setRange(0, 1000)
        mail = self._mail_summary or {"copied": 0, "skipped": 0, "errors": 0, "bytes": [],
                                      "ok": True, "n_ok": 0, "n": 0, "title": ""}
        cloud = getattr(self, "_cloud_stats", [])
        stopped = stopped or getattr(self, "_stopping", False)
        self._stopping = False
        copied = mail["copied"] + sum(c.created + c.updated for c in cloud)
        skipped = mail["skipped"] + sum(c.skipped for c in cloud)
        errors = mail["errors"] + sum(c.failed for c in cloud)
        cloud_bytes = sum(c.bytes for c in cloud)
        rp.t_copied.set(f"{copied:,}")
        rp.t_skipped.set(f"{skipped:,}")
        rp.t_errors.set(f"{errors:,}")
        parts = list(mail["bytes"][:1]) if len(mail["bytes"]) == 1 else \
            ([f"{len(mail['bytes'])} mail runs"] if mail["bytes"] else [])
        if cloud_bytes:
            parts.append(human_bytes(cloud_bytes) + (" files" if parts else ""))
        rp.t_bytes.set(" + ".join(parts) if parts else "—")
        all_ok = mail["ok"] and not any(c.failed for c in cloud)
        if stopped:
            title, color = "Stopped. Run it again any time to continue.", t.AMBER
        elif all_ok:
            rp.bar.setValue(1000)
            rp.percent.setText("100% complete")
            title = ("Simulation finished — nothing was changed." if kind == "dry" else
                     "Migration finished successfully.")
            color = t.GREEN
        elif mail["title"]:
            title, color = mail["title"], t.RED
        elif not mail["ok"]:
            title, color = (f"{mail['n_ok']} of {mail['n']} mailboxes finished without "
                            "email errors. See the table below."), t.AMBER
        else:
            title, color = "Finished with some errors — see the table and the log.", t.AMBER
        rp.status.setText(title)
        rp.status.setStyleSheet(f"color: {color};")
        rp.header.title.setText("Stopped" if stopped else
                                ("Simulation complete" if kind == "dry" else "Migration complete"))
        rp.header.badge.set_icon("check" if all_ok and not stopped else "alert",
                                 t.GREEN_DARK if all_ok and not stopped else t.AMBER)
        verb = "would be copied" if kind == "dry" else "copied"
        rp.detail.setText(f"{copied:,} items {verb}, {skipped:,} already at the destination"
                          + (f", {errors:,} errors." if errors else "."))
        self.log_card.add(title, "ok" if all_ok else "error")
        if all_ok and kind == "migrate":
            self.checks["source"] = self.checks["destination"] = True
        self._record_history(kind, self.last_results, all_ok, title, copied)
        self._notify(title)
        self._update_footer()
        self._tips_from_checks()

        if kind == "dry" and all_ok and getattr(self, "_then_migrate", False):
            self._then_migrate = False
            answer = QMessageBox.question(
                self, APP_NAME,
                f"Simulation finished: {copied:,} items would be copied and {skipped:,} "
                "are already at the destination.\n\nStart the real migration now?")
            if answer == QMessageBox.Yes:
                QTimer.singleShot(0, lambda: self._start_run("migrate"))

    def _record_history(self, kind, results, all_ok, title, copied) -> None:
        s, d = self.src_page.server(), self.dst_page.server()
        accounts = self._accounts_running
        kinds = ", ".join(KIND_NAMES[k] for k in self.data_page.selected())
        self.store.add_history({
            "mode_label": ("Simulation" if kind == "dry" else "Migration") +
                          (f" · {len(accounts)} mailboxes" if len(accounts) > 1 else "") +
                          (f" · {kinds}" if kinds else ""),
            "from": f"{accounts[0].user1 if len(accounts) == 1 else ''} "
                    f"@ {s.host or PROVIDERS[s.provider].host}".strip(),
            "to": f"{accounts[0].user2 if len(accounts) == 1 else ''} "
                  f"@ {d.host or PROVIDERS[d.provider].host}".strip(),
            "result": title, "ok": all_ok, "copied": copied,
            "logs": [r.progress.logfile for r in results if r.progress and r.progress.logfile],
        })

    def _notify(self, message: str) -> None:
        QApplication.alert(self)
        if not self.opt_page.notify.isChecked() or self.isActiveWindow():
            return
        if QSystemTrayIcon.isSystemTrayAvailable():
            if self._tray is None:
                self._tray = QSystemTrayIcon(self.windowIcon(), self)
            self._tray.show()
            self._tray.showMessage(APP_NAME, message, QSystemTrayIcon.Information, 10000)

    # ============================================================== review

    def _fill_review(self) -> None:
        rp = self.review_page
        bulk = self.mode == "bulk"
        rp.type_sec.set_lines([
            label("Bulk Migration" if bulk else "Single Mailbox Migration", "sub"),
            label("Migrate multiple mailboxes between the same two servers." if bulk else
                  "Migrate one mailbox from a source server to a destination server.",
                  "hint", True)])

        def server_lines(page: ServerPage, n: int) -> list:
            s = page.server()
            rows = [("Provider", PROVIDERS[s.provider].name)]
            cred = self.creds.get(n)
            mode = page.auth_mode()
            cloud = page.platform() != "imap"
            if cloud and mode == AUTH_SIGNIN:
                rows.append(("Account", cred.email if cred else "not signed in"))
                rows.append(("Authentication", "Signed in with "
                             + ("Google" if page.platform() == "google" else "Microsoft")))
            elif cloud and mode == AUTH_ADMIN:
                if not bulk:
                    rows.append(("Mailbox", page.user.text().strip()))
                rows.append(("Authentication", "Organization admin access"))
            else:
                if not bulk:
                    rows.append(("Username", page.user.text().strip()))
                rows.append(("Authentication", "OAuth2 access token" if s.auth == "oauth2"
                             else "Password"))
                for key, name in (("carddav", "Contacts"), ("caldav", "Calendars"),
                                  ("webdav", "Files")):
                    url = page.dav().get(key)
                    if url:
                        rows.append((name, url))
            return [label(page.endpoint_text() if not cloud else
                          PROVIDERS[s.provider].name, "sub"), kv(rows)]

        rp.src_sec.set_lines(server_lines(self.src_page, 1))
        rp.dst_sec.set_lines(server_lines(self.dst_page, 2))
        kinds = self.data_page.selected()
        rp.data_sec.set_lines([label(", ".join(KIND_NAMES[k] for k in kinds) or "Nothing",
                                     "sub")])
        if "shared" in kinds:
            plans = self.shared_page.plans()
            rp.data_sec.content.addWidget(label(
                f"{len(plans)} shared drive{'s' if len(plans) != 1 else ''}: " +
                ", ".join(p.source.name for p in plans[:5]) +
                ("…" if len(plans) > 5 else ""), "muted", True))
        rp.mbx_sec.setVisible(bulk)
        if bulk:
            accounts = self.mbx_page.table.accounts()
            shown = [f"{a.user1}  →  {a.user2}" for a in accounts[:4]]
            if len(accounts) > 4:
                shown.append(f"…and {len(accounts) - 4} more")
            rp.mbx_sec.set_lines([label(f"{len(accounts)} mailboxes", "sub")] +
                                 [label(x, "muted") for x in shown])
        lines = (self.opt_page.summary() if "mail" in kinds else []) + \
            self.opt_page.cloud_summary(kinds, self.dst_page.platform())
        rp.opt_sec.set_lines([checked_line(x, x.startswith("⚠")) for x in lines])

    # ================================================================== csv

    def _import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import mailboxes", "",
                                              "CSV files (*.csv *.txt);;All files (*)")
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            QMessageBox.warning(self, APP_NAME, f"Could not read the file:\n{exc}")
            return
        accounts, problems = parse_accounts(text)
        if not accounts:
            QMessageBox.warning(self, APP_NAME, "No mailboxes found in this file.\n\n" +
                                "\n".join(problems[:10]))
            return
        self.mbx_page.set_accounts(accounts)
        self._reset_checks()
        self.log_card.add(f"Imported {len(accounts)} mailboxes from {Path(path).name}", "ok")
        if problems:
            QMessageBox.warning(self, APP_NAME, "Some lines were skipped:\n" +
                                "\n".join(problems[:10]))

    def _export_csv(self) -> None:
        accounts = self.mbx_page.table.accounts()
        if not accounts:
            QMessageBox.information(self, APP_NAME, "There are no mailboxes to export.")
            return
        include = QMessageBox.question(
            self, APP_NAME, "Include passwords in the exported file?\n\nAnyone who can read "
            "the file will see them.", QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No) == QMessageBox.Yes
        path, _ = QFileDialog.getSaveFileName(self, "Export mailboxes", "mailboxes.csv",
                                              "CSV files (*.csv)")
        if path:
            try:
                Path(path).write_text(format_accounts(accounts, include), encoding="utf-8")
                if include:
                    os.chmod(path, 0o600)
            except OSError as exc:
                QMessageBox.warning(self, APP_NAME, f"Could not save the file:\n{exc}")

    def _save_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save CSV template",
                                              "mailboxes-template.csv", "CSV files (*.csv)")
        if path:
            Path(path).write_text(
                "user1;password1;user2;password2\n"
                "alice@old-domain.com;old-password;alice@new-domain.com;new-password\n",
                encoding="utf-8")

    # ================================================================ misc

    def _open_logs(self) -> None:
        logs = [r.progress.logfile for r in self.last_results
                if r.progress and r.progress.logfile and os.path.exists(r.progress.logfile)]
        target = logs[0] if len(logs) == 1 else str(self.store.logs_dir)
        QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    def _new_migration(self) -> None:
        self.on_run_page = False
        self._reset_checks()
        self.src_page.secret.clear()
        self.dst_page.secret.clear()
        for page in (self.src_page, self.dst_page):
            page.tested_ok = False
            page.status.set_state("idle", 'Click "Test Connection" to validate your server '
                                          "settings.")
        self.review_page.set_estimate(None, None, "", "Click Estimate to count the messages "
                                                      "on the source (read-only).")
        self.max_step = 0
        self._go(0)


def self_test() -> int:
    """Check that the bundled imapsync starts. Used by the build pipeline."""
    import subprocess

    engine = find_imapsync()
    print(f"{APP_NAME} {__version__}")
    if not engine:
        print("imapsync: NOT FOUND")
        return 1
    print("imapsync:", " ".join(engine))
    proc = subprocess.run(engine + ["--version"], capture_output=True, text=True,
                          timeout=300)
    print("imapsync version:", proc.stdout.strip() or proc.stderr.strip())
    # The contacts / calendars / files engine and its dependencies must load too.
    try:
        import importlib
        from zoneinfo import ZoneInfo

        for module in ("cryptography.hazmat.primitives.asymmetric.rsa", "icalendar",
                       "mailmigrator.cloud.services"):
            importlib.import_module(module)
        from .cloud.recurrence import rrule_to_graph
        ZoneInfo("America/New_York")
        rrule_to_graph("RRULE:FREQ=WEEKLY;BYDAY=MO", __import__("datetime").date(2025, 1, 6),
                       "UTC")
        print("cloud engine: OK")
    except Exception as exc:  # reported as a build failure
        print("cloud engine: FAILED", exc)
        return 1
    return proc.returncode


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName("MailMigrator")
    QApplication.setApplicationVersion(__version__)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(t.palette())
    app.setStyleSheet(t.stylesheet())
    app.setWindowIcon(app_icon())
    win = MainWindow()
    win.show()
    if os.environ.get("MAILMIGRATOR_SMOKE_TEST"):
        QTimer.singleShot(int(os.environ["MAILMIGRATOR_SMOKE_TEST"]), win.close)
    code = app.exec()
    # Destroy the window while the application still exists; letting Python's
    # shutdown order decide can crash Qt during teardown.
    win.deleteLater()
    app.sendPostedEvents(None, 0)
    from PySide6.QtCore import QEvent
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    del win
    return code
