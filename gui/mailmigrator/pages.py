"""The wizard steps: type, source, destination, mailboxes, options, review, run."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QComboBox, QDateEdit,
                               QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLineEdit,
                               QProgressBar, QRadioButton, QSpinBox, QStyledItemDelegate,
                               QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout,
                               QWidget, QCheckBox)

from . import theme as t
from .engine import (AUTH_OAUTH2, AUTH_PASSWORD, SECURITY_NONE, SECURITY_SSL,
                     SECURITY_STARTTLS, Account, Options, Server)
from .providers import PROVIDER_ORDER, PROVIDERS
from .widgets import (ChoiceCard, FormGrid, IconLabel, PageHeader, StatTile, StatusLine,
                      Toggle, button, card, divider, label)

SECURITY_CHOICES = [
    (SECURITY_SSL, "SSL/TLS (port 993)", 993),
    (SECURITY_STARTTLS, "STARTTLS (port 143)", 143),
    (SECURITY_NONE, "None — unencrypted (port 143)", 143),
]
AUTH_CHOICES = [
    (AUTH_PASSWORD, "Password / app password"),
    (AUTH_OAUTH2, "OAuth2 access token"),
]
SIZE_CHOICES = [10, 25, 50, 100, 200]


def set_combo(combo: QComboBox, data) -> None:
    idx = combo.findData(data)
    if idx >= 0:
        combo.setCurrentIndex(idx)


class Page(QWidget):
    """A wizard step: header plus a body card. Supplies text for the right column."""

    info: tuple[str, str] = ("", "")         # subtitle, body
    tips_title = ""
    tips: list[str] = []

    def __init__(self, icon_name: str, title: str, subtitle: str,
                 color: str = t.ACCENT) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.frame, lay = card()
        lay.setContentsMargins(30, 28, 30, 28)
        lay.setSpacing(22)
        self.header = PageHeader(icon_name, title, subtitle, color)
        lay.addWidget(self.header)
        self.body = lay
        outer.addWidget(self.frame)


# ---------------------------------------------------------------- step 1

class TypePage(Page):
    changed = Signal(str)
    info = ("Choose a migration type",
            "Single moves one mailbox. Bulk moves many mailboxes between the same two "
            "servers, from a table or a CSV file.")
    tips_title = "Good to know"
    tips = ["Nothing is deleted unless you ask for it",
            "You can stop and restart at any time",
            "Re-running only copies new messages",
            "Passwords are never saved"]

    def __init__(self) -> None:
        super().__init__("mail", "Welcome to Mail Migrator",
                         "Move your emails, folders, and mailbox structure from one "
                         "server to another.")
        inner, il = card("inner")
        il.setContentsMargins(28, 26, 28, 28)
        il.setSpacing(8)
        il.addWidget(label("What type of migration do you want to perform?", "h2"))
        il.addWidget(label("Choose the option that best fits your needs. You can change "
                           "this later.", "sub", True))
        row = QHBoxLayout()
        row.setSpacing(20)
        self.single = ChoiceCard(
            "mail", t.ACCENT, "Single Mailbox Migration",
            "Migrate one mailbox from a source server to a destination server.",
            ["Individual users", "Testing migrations", "One-time mailbox moves"])
        self.bulk = ChoiceCard(
            "users", t.PURPLE, "Bulk Migration",
            "Migrate multiple mailboxes from a source server to a destination server.",
            ["Organizations and teams", "Moving many users at once", "CSV import support",
             "Batch processing with progress tracking"])
        row.addWidget(self.single, 1)
        row.addWidget(self.bulk, 1)
        il.addSpacing(14)
        il.addLayout(row)
        self.body.addWidget(inner)

        nxt, nl = card("infoBox")
        nl.setContentsMargins(22, 18, 22, 18)
        h = QHBoxLayout()
        h.setSpacing(18)
        h.addWidget(IconLabel("info", t.ACCENT_HI, 26), 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(4)
        col.addWidget(label("What happens next?", "link"))
        col.addWidget(label("We'll guide you through connecting to your servers, selecting "
                            "mailboxes, and configuring options based on the migration type "
                            "you choose.", "muted", True))
        h.addLayout(col, 1)
        nl.addLayout(h)
        self.body.addWidget(nxt)
        self.body.addStretch(1)

        self.single.clicked.connect(lambda: self.set_mode("single"))
        self.bulk.clicked.connect(lambda: self.set_mode("bulk"))
        self._mode = ""
        self.set_mode("single")

    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        changed = mode != self._mode
        self._mode = mode
        self.single.set_selected(mode == "single")
        self.bulk.set_selected(mode == "bulk")
        if changed:
            self.changed.emit(mode)


# ---------------------------------------------------------------- steps 2, 3

class ServerPage(Page):
    test_requested = Signal()

    def __init__(self, side: int) -> None:
        self.side = side
        word = "source" if side == 1 else "destination"
        if side == 1:
            super().__init__("server", "Connect your source server",
                             "Set up the connection to the current server where your "
                             "mailbox exists.")
            self.info = ("Connect your source server",
                         "This is the server that currently hosts the mailbox you want to "
                         "migrate. We'll use these settings to connect and verify access "
                         "to your emails.")
            self.tips_title = "Tips for a successful connection"
            self.tips = ["Use the correct server hostname (e.g. imap.example.com)",
                         "Choose the right security type (SSL/TLS is recommended)",
                         "Verify your username and password are correct",
                         "Use an app password if you have two-factor authentication enabled"]
        else:
            super().__init__("server", "Connect your destination server",
                             "Set up the connection to the destination server where your "
                             "mailbox will be migrated to.")
            self.info = ("Connect your destination server",
                         "This is the server where your mailbox will be migrated to. We'll "
                         "use these settings to connect and verify that we can access your "
                         "destination mailbox.")
            self.tips_title = "Tips for a successful destination setup"
            self.tips = ["Use the correct server hostname (e.g. imap.example.com)",
                         "Choose the right security type (SSL/TLS is recommended)",
                         "Verify your username and password are correct",
                         "Ensure the destination mailbox has enough storage space",
                         "Test the connection before continuing"]

        inner, il = card("inner")
        il.setContentsMargins(26, 22, 26, 22)
        il.setSpacing(4)
        il.addWidget(label("Server connection details" if side == 1 else
                           "Destination server connection details", "h3"))
        il.addWidget(label(f"Enter the connection settings for your {word} mail server.",
                           "muted", True))
        il.addSpacing(14)

        self.provider = QComboBox()
        for key in PROVIDER_ORDER:
            self.provider.addItem(PROVIDERS[key].name, key)
        self.host = QLineEdit()
        self.host.setPlaceholderText("imap.example.com")
        self.security = QComboBox()
        for key, text, _port in SECURITY_CHOICES:
            self.security.addItem(text, key)
        self.port = QSpinBox()
        self.port.setRange(0, 65535)
        self.port.setSpecialValueText("Default")
        self.auth = QComboBox()
        for key, text in AUTH_CHOICES:
            self.auth.addItem(text, key)
        self.user = QLineEdit()
        self.user.setPlaceholderText("name@example.com")
        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.Password)
        self.eye = QToolButton()
        self.eye.setObjectName("eye")
        self.eye.setCheckable(True)
        self.eye.setCursor(Qt.PointingHandCursor)
        self.eye.setIcon(t.icon("eye-off", t.MUTED))
        self.eye.toggled.connect(self._toggle_secret)
        secret_row = QHBoxLayout()
        secret_row.setSpacing(6)
        secret_row.addWidget(self.secret, 1)
        secret_row.addWidget(self.eye)

        grid = FormGrid()
        grid.add("provider", "box", "Provider", self.provider)
        self.provider_hint = label("", "hint", True)
        grid.add_widget(self.provider_hint)
        self.host_hint = grid.add("host", "server", "Server", self.host,
                                  "Hostname or IP address of your IMAP server.")
        grid.add("security", "lock", "Security", self.security)
        grid.add("port", "network", "Port", self.port)
        grid.add_widget(divider())
        spacer = QWidget()
        spacer.setFixedHeight(10)
        grid.add_widget(spacer)
        grid.add("auth", "user", "Sign in with", self.auth)
        grid.add("user", "at", "Username", self.user, "Your full email address.")
        self.secret_label_row = grid.add(
            "secret", "key", "Password", secret_row,
            "Use your email password or an app password if required.")
        self.grid = grid
        il.addLayout(grid)

        self.test_btn = button("Test Connection", "wifi", "test", "#ffffff")
        self.test_btn.clicked.connect(self.test_requested)
        test_row = QHBoxLayout()
        test_row.addWidget(self.test_btn)
        test_row.addStretch(1)
        il.addLayout(test_row)
        il.addSpacing(8)
        self.status = StatusLine('Click "Test Connection" to validate your server settings.')
        il.addWidget(self.status)
        self.bulk_note = label("In a bulk migration, usernames and passwords are entered "
                               "per mailbox in the Mailboxes step, and every login is "
                               "checked on the Review step.", "muted", True)
        il.addWidget(self.bulk_note)
        self.body.addWidget(inner)
        self.body.addStretch(1)

        self._last_provider = "custom"
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.security.currentIndexChanged.connect(self._security_changed)
        self.auth.currentIndexChanged.connect(self._auth_changed)
        for w in (self.host, self.user, self.secret):
            w.textEdited.connect(self._invalidate)
        for w in (self.provider, self.security, self.auth):
            w.currentIndexChanged.connect(self._invalidate)
        self.port.valueChanged.connect(self._invalidate)
        self.tested_ok = False
        self._provider_changed()
        self.set_bulk(False)

    # -- behaviour --

    def _toggle_secret(self, on: bool) -> None:
        self.secret.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)
        self.eye.setIcon(t.icon("eye" if on else "eye-off", t.MUTED))

    def _invalidate(self, *_):
        if self.tested_ok:
            self.tested_ok = False
            self.status.set_state("idle", 'Settings changed. Click "Test Connection" to '
                                          "check them again.")

    def _provider_changed(self) -> None:
        p = PROVIDERS[self.provider.currentData()]
        locked = p.flag in ("gmail", "office")
        if p.host:
            self.host.setText(p.host)
            set_combo(self.security, p.security)
            self.port.setValue(p.port)
        elif PROVIDERS[self._last_provider].host:
            self.host.clear()
            set_combo(self.security, SECURITY_SSL)
            self.port.setValue(0)
        self._last_provider = p.key
        self.host.setReadOnly(locked)
        self.security.setEnabled(not locked)
        self.port.setEnabled(not locked)
        self.provider_hint.setText(p.hint if p.key != "custom" else "")
        self.provider_hint.setVisible(bool(p.hint) and p.key != "custom")
        if p.key == "office365":
            set_combo(self.auth, AUTH_OAUTH2)
        self._security_changed()
        self._auth_changed()

    def _security_changed(self) -> None:
        """Keep the port on the standard one for the chosen security, unless customised."""
        if PROVIDERS[self.provider.currentData()].port:
            return
        if self.port.value() in (0, 993, 143):
            default = {key: port for key, _text, port in SECURITY_CHOICES}
            self.port.blockSignals(True)
            self.port.setValue(default[self.security.currentData()])
            self.port.blockSignals(False)

    def _auth_changed(self) -> None:
        oauth = self.auth.currentData() == AUTH_OAUTH2
        row = self.grid.rows["secret"]
        row[1].setText("Access token" if oauth else "Password")
        self.secret.setPlaceholderText("Paste the OAuth2 access token" if oauth else "")
        if self.secret_label_row:
            self.secret_label_row.setText(
                "A token issued for this mailbox by your provider (OAuth2)." if oauth else
                "Use your email password or an app password if required.")

    def set_bulk(self, bulk: bool) -> None:
        for key in ("user", "secret"):
            self.grid.set_visible(key, not bulk)
        self.test_btn.setVisible(not bulk)
        self.status.setVisible(not bulk)
        self.bulk_note.setVisible(bulk)

    def set_testing(self, running: bool) -> None:
        self.test_btn.setEnabled(not running)
        if running:
            self.status.set_state("running", "Connecting and signing in…")

    def set_test_result(self, ok: bool, message: str) -> None:
        self.tested_ok = ok
        self.status.set_state("ok" if ok else "fail", message)

    # -- data --

    def server(self) -> Server:
        return Server(provider=self.provider.currentData(), host=self.host.text().strip(),
                      port=self.port.value(), security=self.security.currentData(),
                      auth=self.auth.currentData())

    def set_server(self, s: Server) -> None:
        set_combo(self.provider, s.provider)
        self._provider_changed()
        if not self.host.isReadOnly():
            self.host.setText(s.host)
        if self.security.isEnabled():
            set_combo(self.security, s.security)
        if self.port.isEnabled():
            self.port.setValue(s.port)
        set_combo(self.auth, s.auth)

    def endpoint_text(self) -> str:
        s = self.server()
        p = PROVIDERS[s.provider]
        host = s.host or p.host or "(not set)"
        port = s.port or (993 if s.security == SECURITY_SSL else 143)
        sec = {SECURITY_SSL: "SSL/TLS", SECURITY_STARTTLS: "STARTTLS",
               SECURITY_NONE: "no encryption"}[s.security]
        return f"{host}:{port} (IMAP, {sec})"


# ---------------------------------------------------------------- bulk step

class SecretDelegate(QStyledItemDelegate):
    def displayText(self, value, locale) -> str:  # noqa: N802
        return "•" * min(len(str(value or "")), 10)

    def createEditor(self, parent, option, index):  # noqa: N802
        editor = QLineEdit(parent)
        editor.setEchoMode(QLineEdit.Password)
        return editor


class AccountsTable(QTableWidget):
    COLUMNS = ["Source user", "Password", "Destination user", "Password", "Status"]

    def __init__(self) -> None:
        super().__init__(0, len(self.COLUMNS))
        self.setHorizontalHeaderLabels(self.COLUMNS)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setShowGrid(False)
        self.setAlternatingRowColors(False)
        header = self.horizontalHeader()
        header.setMinimumSectionSize(50)
        for col in (0, 2, 4):
            header.setSectionResizeMode(col, QHeaderView.Stretch)
        for col in (1, 3):
            header.setSectionResizeMode(col, QHeaderView.Fixed)
            self.setColumnWidth(col, 100)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.ElideMiddle)
        delegate = SecretDelegate(self)
        self.setItemDelegateForColumn(1, delegate)
        self.setItemDelegateForColumn(3, delegate)
        self.setMinimumHeight(300)
        self.verticalHeader().setDefaultSectionSize(40)

    def add_account(self, a: Account | None = None) -> None:
        a = a or Account()
        row = self.rowCount()
        self.insertRow(row)
        for col, value in enumerate((a.user1, a.secret1, a.user2, a.secret2)):
            self.setItem(row, col, QTableWidgetItem(value))
        status = QTableWidgetItem("")
        status.setFlags(status.flags() & ~Qt.ItemIsEditable)
        self.setItem(row, 4, status)

    def _values(self, row: int) -> list[str]:
        return [(self.item(row, c).text() if self.item(row, c) else "") for c in range(4)]

    def _nonblank_rows(self) -> list[int]:
        return [r for r in range(self.rowCount()) if any(v.strip() for v in self._values(r))]

    def accounts(self) -> list[Account]:
        out = []
        for r in self._nonblank_rows():
            v = self._values(r)
            out.append(Account(v[0].strip(), v[1], v[2].strip(), v[3]))
        return out

    def set_status(self, index: int, text: str, color: str | None = None) -> None:
        rows = self._nonblank_rows()
        if 0 <= index < len(rows):
            item = self.item(rows[index], 4)
            item.setText(text)
            item.setToolTip(text)
            item.setForeground(QColor(color or t.TEXT))

    def clear_status(self) -> None:
        for r in range(self.rowCount()):
            if self.item(r, 4):
                self.item(r, 4).setText("")


class MailboxesPage(Page):
    info = ("Add the mailboxes to migrate",
            "Each row is one mailbox: its login on the source server and its login on "
            "the destination server. The servers you chose in the previous steps are used "
            "for every row.")
    tips_title = "Tips for bulk migrations"
    tips = ["Import a CSV file: user1;password1;user2;password2",
            "Use app passwords where providers require them",
            "Mailboxes are migrated one after the other",
            "Each mailbox gets its own status and log"]

    import_requested = Signal()
    export_requested = Signal()
    template_requested = Signal()

    def __init__(self) -> None:
        super().__init__("users", "Choose the mailboxes",
                         "List every mailbox to move, with its source and destination "
                         "login.", t.PURPLE)
        inner, il = card("inner")
        il.setContentsMargins(24, 22, 24, 22)
        top = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label("Mailboxes", "h3"))
        self.count = label("", "muted")
        col.addWidget(self.count)
        top.addLayout(col, 1)
        self.import_btn = button("Import CSV…", "upload", obj="small")
        self.export_btn = button("Export CSV…", "download", obj="small")
        self.template_btn = button("CSV template", "file", obj="small")
        self.add_btn = button("Add mailbox", "plus", obj="small")
        self.remove_btn = button("Remove", "trash", obj="small")
        for b in (self.add_btn, self.remove_btn):
            top.addWidget(b)
        il.addLayout(top)
        tools = QHBoxLayout()
        tools.addWidget(label("Double-click a cell to type, or import a CSV file.", "hint"), 1)
        for b in (self.template_btn, self.import_btn, self.export_btn):
            tools.addWidget(b)
        il.addLayout(tools)
        self.table = AccountsTable()
        il.addWidget(self.table, 1)
        self.body.addWidget(inner, 1)

        self.add_btn.clicked.connect(lambda: self.table.add_account())
        self.remove_btn.clicked.connect(self._remove)
        self.import_btn.clicked.connect(self.import_requested)
        self.export_btn.clicked.connect(self.export_requested)
        self.template_btn.clicked.connect(self.template_requested)
        self.table.itemChanged.connect(self._update_count)
        self.table.model().rowsInserted.connect(self._update_count)
        self.table.model().rowsRemoved.connect(self._update_count)
        self.table.add_account()
        self._update_count()

    def _remove(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)
        if self.table.rowCount() == 0:
            self.table.add_account()

    def _update_count(self, *_):
        n = len(self.table.accounts())
        self.count.setText(f"{n} mailbox{'es' if n != 1 else ''} listed")

    def set_accounts(self, accounts: list[Account]) -> None:
        self.table.setRowCount(0)
        for a in accounts:
            self.table.add_account(a)
        if not accounts:
            self.table.add_account()


# ---------------------------------------------------------------- options

class OptionSection(QWidget):
    def __init__(self, icon_name: str, title: str, subtitle: str) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 6)
        lay.setSpacing(18)
        lay.addWidget(IconLabel(icon_name, t.MUTED, 24), 0, Qt.AlignTop)
        self.col = QVBoxLayout()
        self.col.setSpacing(4)
        self.col.addWidget(label(title, "h3"))
        self.col.addWidget(label(subtitle, "muted", True))
        self.col.addSpacing(8)
        lay.addLayout(self.col, 1)

    def add_option(self, widget: QWidget, description: str = "") -> None:
        self.col.addWidget(widget)
        if description:
            d = label(description, "hint", True)
            d.setContentsMargins(34, 0, 0, 6)
            self.col.addWidget(d)

    def add_layout(self, layout) -> None:
        self.col.addLayout(layout)


class OptionsPage(Page):
    info = ("Choose migration options",
            "Configure how your emails, folders, and metadata should be copied to the "
            "destination server. You can use the recommended settings or customize them "
            "based on your needs.")
    tips_title = "Recommended settings for most users"
    tips = ["Match standard folders", "Migrate all messages", "Verify server certificates",
            "Run a dry-run simulation first"]

    def __init__(self) -> None:
        super().__init__("settings", "Choose migration options",
                         "Fine-tune how your mail is copied from the source to the "
                         "destination server.")
        inner, il = card("inner")
        il.setContentsMargins(22, 18, 22, 18)
        il.setSpacing(10)

        folders = OptionSection("folder", "Folder Handling",
                                "Choose how to handle mailbox folders during migration.")
        self.automap = QCheckBox("Match standard folders")
        self.automap.setChecked(True)
        folders.add_option(self.automap, "Map Sent, Drafts, Trash and Junk to the "
                                         "destination's own standard folders.")
        self.just_folders = QCheckBox("Copy folder structure only")
        folders.add_option(self.just_folders, "Recreate the folder structure without "
                                              "migrating messages.")
        il.addWidget(folders)
        il.addWidget(divider())

        filters = OptionSection("mail", "Message Filters",
                                "Choose which messages to migrate (optional).")
        self.all_msgs = QRadioButton("Migrate all messages")
        self.range_msgs = QRadioButton("Migrate messages from a specific date range")
        group = QButtonGroup(self)
        group.addButton(self.all_msgs)
        group.addButton(self.range_msgs)
        self.all_msgs.setChecked(True)
        filters.add_option(self.all_msgs, "Copy all messages from the source mailbox.")
        filters.add_option(self.range_msgs)
        dates = QHBoxLayout()
        dates.setContentsMargins(34, 0, 0, 4)
        self.date_from = QDateEdit(QDate.currentDate().addYears(-5))
        self.date_to = QDateEdit(QDate.currentDate())
        for d in (self.date_from, self.date_to):
            d.setCalendarPopup(True)
            d.setDisplayFormat("MMM d, yyyy")
            d.setMaximumDate(QDate.currentDate())
        dates.addWidget(self.date_from, 1)
        dates.addWidget(label("to", "muted"))
        dates.addWidget(self.date_to, 1)
        filters.add_layout(dates)
        size_row = QHBoxLayout()
        self.limit_size = QCheckBox("Limit by message size")
        self.size_combo = QComboBox()
        for mb in SIZE_CHOICES:
            self.size_combo.addItem(f"{mb} MB", mb)
        set_combo(self.size_combo, 50)
        size_row.addWidget(self.limit_size)
        size_row.addWidget(self.size_combo)
        size_row.addStretch(1)
        filters.add_layout(size_row)
        d = label("Only migrate messages smaller than the specified size.", "hint")
        d.setContentsMargins(34, 0, 0, 0)
        filters.col.addWidget(d)
        il.addWidget(filters)
        il.addWidget(divider())

        sync = OptionSection("refresh", "Sync Behavior",
                             "imapsync always works this way — nothing to set.")
        for title, text in (
                ("Skip duplicates", "Messages already on the destination are not copied "
                                    "again."),
                ("Incremental sync", "Run the migration again later to copy only new "
                                     "messages."),
                ("Preserve read/unread status", "Read, flagged and answered marks are "
                                                "copied with each message.")):
            row = QHBoxLayout()
            row.setSpacing(12)
            row.addWidget(IconLabel("check-square", t.ACCENT_HI, 20))
            row.addWidget(label(title))
            hint = label(text, "hint", True)
            row.addWidget(hint, 1)
            sync.add_layout(row)
        il.addWidget(sync)

        # Advanced (collapsible)
        adv_frame, al = card("infoBox")
        al.setContentsMargins(18, 12, 18, 12)
        self.adv_toggle = QToolButton()
        self.adv_toggle.setObjectName("disclosure")
        self.adv_toggle.setCheckable(True)
        self.adv_toggle.setCursor(Qt.PointingHandCursor)
        head = QHBoxLayout()
        head.setSpacing(16)
        head.addWidget(IconLabel("settings", t.MUTED, 24))
        hcol = QVBoxLayout()
        hcol.setSpacing(2)
        hcol.addWidget(label("Advanced Options", "h3"))
        hcol.addWidget(label("Certificates, folder selection, deletions and extra imapsync "
                             "arguments for experienced users.", "muted", True))
        head.addLayout(hcol, 1)
        self.adv_toggle.setIcon(t.icon("chevron-down", t.MUTED))
        head.addWidget(self.adv_toggle)
        al.addLayout(head)
        self.adv = QWidget()
        grid = QGridLayout(self.adv)
        grid.setContentsMargins(40, 12, 0, 4)
        grid.setVerticalSpacing(10)
        self.verify = QCheckBox("Verify server certificates (recommended)")
        self.verify.setChecked(True)
        self.verify.setToolTip("Untick only for servers with a self-signed certificate "
                               "that you trust.")
        self.folders = QLineEdit()
        self.folders.setPlaceholderText("All folders (or e.g. INBOX, Projects)")
        self.exclude = QLineEdit()
        self.exclude.setPlaceholderText("Regular expression, e.g. ^Spam$|^Trash$")
        self.subfolder = QLineEdit()
        self.subfolder.setPlaceholderText("e.g. Imported from old server")
        self.delete2 = QCheckBox("Mirror: delete destination messages not on the source")
        self.delete1 = QCheckBox("Move: delete SOURCE messages after copying")
        self.delete1.setStyleSheet(f"color: {t.RED};")
        self.extra = QLineEdit()
        self.extra.setPlaceholderText("e.g. --skipsize --nofoldersizes")
        r = 0
        grid.addWidget(self.verify, r, 0, 1, 2)
        r += 1
        for text, w in (("Only these folders", self.folders),
                        ("Skip folders matching", self.exclude),
                        ("Put everything inside folder", self.subfolder)):
            grid.addWidget(label(text), r, 0)
            grid.addWidget(w, r, 1)
            r += 1
        grid.addWidget(self.delete2, r, 0, 1, 2)
        r += 1
        grid.addWidget(self.delete1, r, 0, 1, 2)
        r += 1
        grid.addWidget(label("Extra imapsync arguments"), r, 0)
        grid.addWidget(self.extra, r, 1)
        grid.setColumnStretch(1, 1)
        al.addWidget(self.adv)
        self.adv.setVisible(False)
        self.adv_toggle.toggled.connect(self._toggle_adv)
        adv_frame.mousePressEvent = lambda _e: self.adv_toggle.toggle()
        il.addWidget(adv_frame)
        self.body.addWidget(inner)

        # Bottom toggles
        bar, bl = card("inner")
        bl.setContentsMargins(20, 14, 20, 14)
        h = QHBoxLayout()
        h.setSpacing(16)
        self.simulate_first = Toggle()
        self.simulate_first.setChecked(True)
        c1 = QVBoxLayout()
        c1.setSpacing(2)
        c1.addWidget(label("<b>Run a dry-run simulation first</b> (recommended)", wrap=True))
        c1.addWidget(label("Preview what will be migrated before anything is copied.",
                           "hint", True))
        h.addWidget(self.simulate_first)
        h.addLayout(c1, 1)
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet(f"color: {t.BORDER};")
        h.addWidget(sep)
        h.addWidget(IconLabel("bell", t.ACCENT_HI, 22))
        c2 = QVBoxLayout()
        c2.setSpacing(2)
        c2.addWidget(label("<b>Notify me when complete</b>", wrap=True))
        c2.addWidget(label("Desktop notification when the migration finishes.", "hint",
                           True))
        h.addLayout(c2, 1)
        self.notify = Toggle()
        self.notify.setChecked(True)
        h.addWidget(self.notify)
        bl.addLayout(h)
        self.body.addWidget(bar)

        self.range_msgs.toggled.connect(self._sync_enabled)
        self.limit_size.toggled.connect(self._sync_enabled)
        self._sync_enabled()

    def _toggle_adv(self, on: bool) -> None:
        self.adv.setVisible(on)
        self.adv_toggle.setIcon(t.icon("chevron-up" if on else "chevron-down", t.MUTED))

    def _sync_enabled(self, *_):
        self.date_from.setEnabled(self.range_msgs.isChecked())
        self.date_to.setEnabled(self.range_msgs.isChecked())
        self.size_combo.setEnabled(self.limit_size.isChecked())

    def options(self) -> Options:
        max_age = min_age = 0
        if self.range_msgs.isChecked():
            today = date.today()
            d_from = self.date_from.date().toPython()
            d_to = self.date_to.date().toPython()
            if d_from > d_to:
                d_from, d_to = d_to, d_from
            # imapsync filters on age in days: newer than maxage, older than minage.
            max_age = max(1, (today - d_from).days + 1)
            min_age = max(0, (today - d_to).days)
        folders = [f.strip() for f in self.folders.text().split(",") if f.strip()]
        return Options(
            automap=self.automap.isChecked(),
            just_folders=self.just_folders.isChecked(),
            verify_certs=self.verify.isChecked(),
            max_age_days=max_age,
            min_age_days=min_age,
            max_size_mb=self.size_combo.currentData() if self.limit_size.isChecked() else 0,
            only_folders=folders,
            exclude=self.exclude.text(),
            subfolder2=self.subfolder.text(),
            delete2=self.delete2.isChecked(),
            delete1=self.delete1.isChecked(),
            extra_args=self.extra.text(),
        )

    def extras(self) -> dict:
        return {"simulate_first": self.simulate_first.isChecked(),
                "notify": self.notify.isChecked(),
                "date_range": ([self.date_from.date().toString(Qt.ISODate),
                                self.date_to.date().toString(Qt.ISODate)]
                               if self.range_msgs.isChecked() else None)}

    def set_options(self, o: Options, extras: dict | None = None) -> None:
        extras = extras or {}
        self.automap.setChecked(o.automap)
        self.just_folders.setChecked(o.just_folders)
        self.verify.setChecked(o.verify_certs)
        rng = extras.get("date_range")
        if rng and len(rng) == 2:
            self.range_msgs.setChecked(True)
            self.date_from.setDate(QDate.fromString(rng[0], Qt.ISODate))
            self.date_to.setDate(QDate.fromString(rng[1], Qt.ISODate))
        else:
            self.all_msgs.setChecked(True)
        self.limit_size.setChecked(bool(o.max_size_mb))
        if o.max_size_mb:
            if self.size_combo.findData(o.max_size_mb) < 0:
                self.size_combo.addItem(f"{o.max_size_mb} MB", o.max_size_mb)
            set_combo(self.size_combo, o.max_size_mb)
        self.folders.setText(", ".join(o.only_folders))
        self.exclude.setText(o.exclude)
        self.subfolder.setText(o.subfolder2)
        self.delete2.setChecked(o.delete2)
        self.delete1.setChecked(o.delete1)
        self.extra.setText(o.extra_args)
        if "simulate_first" in extras:
            self.simulate_first.setChecked(bool(extras["simulate_first"]))
        if "notify" in extras:
            self.notify.setChecked(bool(extras["notify"]))
        if any((o.only_folders, o.exclude, o.subfolder2, o.delete1, o.delete2,
                o.extra_args, not o.verify_certs)):
            self.adv_toggle.setChecked(True)

    def summary(self) -> list[str]:
        o = self.options()
        lines = []
        if o.just_folders:
            lines.append("Copy folder structure only (no messages)")
        elif o.only_folders:
            lines.append("Migrate folders: " + ", ".join(o.only_folders) +
                         " (with subfolders)")
        else:
            lines.append("Migrate all folders (including subfolders)")
        if o.automap:
            lines.append("Match standard folders (Sent, Drafts, Trash, Junk)")
        if self.range_msgs.isChecked():
            lines.append(f"Messages from {self.date_from.date().toString('MMM d, yyyy')} "
                         f"to {self.date_to.date().toString('MMM d, yyyy')}")
        if o.max_size_mb:
            lines.append(f"Skip messages larger than {o.max_size_mb} MB")
        lines.append("Copy email flags (read/unread, starred) · skip duplicates")
        if o.exclude.strip():
            lines.append(f"Skip folders matching {o.exclude.strip()}")
        if o.subfolder2.strip():
            lines.append(f"Put everything inside “{o.subfolder2.strip()}”")
        if not o.verify_certs:
            lines.append("⚠ Server certificates are not verified")
        if o.delete2:
            lines.append("⚠ Delete destination messages not on the source")
        if o.delete1:
            lines.append("⚠ Delete source messages after copying (move)")
        if o.extra_args.strip():
            lines.append(f"Extra arguments: {o.extra_args.strip()}")
        return lines


# ---------------------------------------------------------------- review

class ReviewSection(QFrame):
    edit_clicked = Signal()

    def __init__(self, icon_name: str, color: str, title: str) -> None:
        super().__init__()
        self.setObjectName("inner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 16, 16, 16)
        lay.setSpacing(18)
        from .widgets import IconBadge
        lay.addWidget(IconBadge(icon_name, color, 56), 0, Qt.AlignTop)
        self.col = QVBoxLayout()
        self.col.setSpacing(4)
        self.col.addWidget(label(title, "h3"))
        self.content = QVBoxLayout()
        self.content.setSpacing(4)
        self.col.addLayout(self.content)
        lay.addLayout(self.col, 1)
        self.edit = button("Edit", "pencil", obj="small")
        self.edit.clicked.connect(self.edit_clicked)
        lay.addWidget(self.edit, 0, Qt.AlignTop)

    def set_lines(self, widgets: list[QWidget]) -> None:
        while self.content.count():
            item = self.content.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                _clear_layout(item.layout())
        for w in widgets:
            self.content.addWidget(w)


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _clear_layout(item.layout())


def kv(rows: list[tuple[str, str]]) -> QWidget:
    w = QWidget()
    g = QGridLayout(w)
    g.setContentsMargins(0, 2, 0, 0)
    g.setHorizontalSpacing(24)
    g.setVerticalSpacing(4)
    for i, (k, v) in enumerate(rows):
        g.addWidget(label(k, "muted"), i, 0)
        vl = label(v, wrap=True)
        vl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        g.addWidget(vl, i, 1)
    g.setColumnStretch(1, 1)
    return w


def checked_line(text: str, warn: bool = False) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(10)
    h.addWidget(IconLabel("alert" if warn else "check", t.AMBER if warn else t.GREEN, 16))
    h.addWidget(label(text.lstrip("⚠ "), wrap=True), 1)
    return w


class ReviewPage(Page):
    info = ("Review and start migration",
            "Please review all your settings to make sure everything is correct. You can "
            "go back to any step using the Back button or Edit. When you're ready, start "
            "the migration.")
    estimate_requested = Signal()
    edit_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__("check-square", "Review and start migration",
                         "Confirm all settings below before starting the migration "
                         "process.")
        self.body.setSpacing(12)
        self.type_sec = ReviewSection("mail", t.ACCENT, "Migration Type")
        self.src_sec = ReviewSection("server", t.ACCENT, "Source Server")
        self.dst_sec = ReviewSection("server", t.PURPLE, "Destination Server")
        self.mbx_sec = ReviewSection("users", t.PURPLE, "Mailboxes")
        self.opt_sec = ReviewSection("settings", t.GREEN_DARK, "Selected Options")
        for sec, key in ((self.type_sec, "type"), (self.src_sec, "source"),
                         (self.dst_sec, "destination"), (self.mbx_sec, "mailboxes"),
                         (self.opt_sec, "options")):
            sec.edit_clicked.connect(lambda k=key: self.edit_requested.emit(k))
            self.body.addWidget(sec)

        est = QFrame()
        est.setObjectName("inner")
        el = QHBoxLayout(est)
        el.setContentsMargins(20, 16, 16, 16)
        el.setSpacing(18)
        from .widgets import IconBadge
        el.addWidget(IconBadge("chart", t.PURPLE, 56), 0, Qt.AlignTop)
        ecol = QVBoxLayout()
        ecol.setSpacing(10)
        top = QHBoxLayout()
        top.addWidget(label("Estimated migration summary", "h3"), 1)
        self.estimate_btn = button("Estimate", "refresh", obj="small")
        self.estimate_btn.clicked.connect(self.estimate_requested)
        top.addWidget(self.estimate_btn)
        ecol.addLayout(top)
        tiles = QHBoxLayout()
        tiles.setSpacing(28)
        self.t_messages = StatTile("mail", "Total messages")
        self.t_folders = StatTile("folder", "Folders")
        self.t_size = StatTile("database", "Estimated size")
        for tile in (self.t_messages, self.t_folders, self.t_size):
            tiles.addWidget(tile, 1)
        ecol.addLayout(tiles)
        self.estimate_note = label("Click Estimate to count the messages on the source "
                                   "(read-only, takes a moment).", "hint", True)
        ecol.addWidget(self.estimate_note)
        el.addLayout(ecol, 1)
        self.body.addWidget(est)
        self.body.addStretch(1)

    def set_estimate(self, messages: int | None, folders: int | None, size: str,
                     note: str) -> None:
        self.t_messages.set(f"{messages:,}" if messages is not None else "—")
        self.t_folders.set(f"{folders:,}" if folders is not None else "—")
        self.t_size.set(size or "—")
        self.estimate_note.setText(note)


# ---------------------------------------------------------------- run

class RunPage(Page):
    info = ("Migration in progress",
            "You can keep using your computer. Stopping is safe: run the migration again "
            "later and it continues where it left off, without duplicates.")

    def __init__(self) -> None:
        super().__init__("refresh", "Migrating your mail…",
                         "Messages are being copied from the source to the destination.")
        inner, il = card("inner")
        il.setContentsMargins(26, 24, 26, 24)
        il.setSpacing(12)
        self.status = label("Starting…", "h2", True)
        il.addWidget(self.status)
        self.detail = label("", "muted", True)
        il.addWidget(self.detail)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        il.addWidget(self.bar)
        self.percent = label("", "hint")
        il.addWidget(self.percent)
        il.addSpacing(8)
        tiles = QHBoxLayout()
        tiles.setSpacing(24)
        self.t_copied = StatTile("mail", "Copied")
        self.t_skipped = StatTile("check", "Already there")
        self.t_errors = StatTile("alert", "Errors")
        self.t_bytes = StatTile("database", "Transferred")
        for tile in (self.t_copied, self.t_skipped, self.t_errors, self.t_bytes):
            tiles.addWidget(tile, 1)
        il.addLayout(tiles)
        self.body.addWidget(inner)

        self.table_frame, tl = card("inner")
        tl.setContentsMargins(22, 18, 22, 18)
        tl.addWidget(label("Mailboxes", "h3"))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Source", "Destination", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        for c in range(3):
            hh.setSectionResizeMode(c, QHeaderView.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setMinimumHeight(180)
        tl.addWidget(self.table)
        self.body.addWidget(self.table_frame, 1)
        self.body.addStretch(1)

    def reset(self, mode: str, accounts: list[Account]) -> None:
        titles = {"dry": ("Simulating your migration…",
                          "Nothing is copied or changed: this shows what would happen."),
                  "migrate": ("Migrating your mail…",
                              "Messages are being copied from the source to the "
                              "destination."),
                  "test": ("Checking connections…", "Signing in to every mailbox."),
                  "sizes": ("Counting messages…", "Reading folder sizes on both servers.")}
        title, sub = titles[mode]
        self.header.title.setText(title)
        self.header.subtitle.setText(sub)
        self.header.badge.set_icon("play" if mode == "migrate" else "refresh", t.ACCENT)
        self.status.setText("Starting…")
        self.status.setStyleSheet("")
        self.detail.setText("")
        self.percent.setText("")
        self.bar.setRange(0, 0)
        for tile in (self.t_copied, self.t_skipped, self.t_errors, self.t_bytes):
            tile.set("—")
        self.t_copied.caption.setText("Would copy" if mode == "dry" else "Copied")
        self.table.setRowCount(0)
        for a in accounts:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(a.user1))
            self.table.setItem(r, 1, QTableWidgetItem(a.user2))
            self.table.setItem(r, 2, QTableWidgetItem("Waiting"))
        self.table_frame.setVisible(len(accounts) > 1)

    def set_row(self, index: int, text: str, color: str = t.TEXT) -> None:
        if 0 <= index < self.table.rowCount():
            item = self.table.item(index, 2)
            item.setText(text)
            item.setForeground(QColor(color))
            item.setToolTip(text)
