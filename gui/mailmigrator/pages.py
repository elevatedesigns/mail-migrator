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
from .engine import (AUTH_ADMIN, AUTH_OAUTH2, AUTH_PASSWORD, AUTH_SIGNIN, SECURITY_NONE,
                     SECURITY_SSL, SECURITY_STARTTLS, Account, Options, Server)
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

CLOUD_PLATFORM = {"gmail": "google", "office365": "microsoft"}
AUTH_FOR = {
    "google": [(AUTH_SIGNIN, "Sign in with Google"),
               (AUTH_ADMIN, "Workspace admin (whole organization)"),
               (AUTH_PASSWORD, "App password (email only)")],
    "microsoft": [(AUTH_SIGNIN, "Sign in with Microsoft"),
                  (AUTH_ADMIN, "Microsoft 365 admin (whole organization)"),
                  (AUTH_OAUTH2, "Paste an access token (email only)")],
    "imap": AUTH_CHOICES,
}


class ServerPage(Page):
    test_requested = Signal()
    signin_requested = Signal()
    admin_requested = Signal()

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
            self.tips = ["Google Workspace and Microsoft 365: sign in to move mail, "
                         "contacts, calendars and files",
                         "Other providers: use the correct server hostname",
                         "Use an app password if you have two-factor authentication enabled",
                         "IT admins can migrate a whole organization without passwords"]
        else:
            super().__init__("server", "Connect your destination server",
                             "Set up the connection to the destination server where your "
                             "mailbox will be migrated to.")
            self.info = ("Connect your destination server",
                         "This is the server where your mailbox will be migrated to. We'll "
                         "use these settings to connect and verify that we can access your "
                         "destination mailbox.")
            self.tips_title = "Tips for a successful destination setup"
            self.tips = ["Contacts, calendars and files can go to Google Workspace or "
                         "Microsoft 365",
                         "Other providers receive email only",
                         "Ensure the destination has enough storage space",
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

        # Browser sign-in
        self.signin_btn = button("Sign in", "user", "test", "#ffffff")
        self.signin_btn.clicked.connect(self.signin_requested)
        self.account_label = label("Not signed in", "muted", True)
        signin_row = QHBoxLayout()
        signin_row.setSpacing(14)
        signin_row.addWidget(self.signin_btn)
        signin_row.addWidget(self.account_label, 1)

        # Admin: Google service account
        self.sa_file = QLineEdit()
        self.sa_file.setPlaceholderText("service-account-key.json")
        self.sa_browse = button("Browse…", "folder-open", obj="small")
        self.sa_browse.clicked.connect(self._browse_key)
        sa_row = QHBoxLayout()
        sa_row.setSpacing(6)
        sa_row.addWidget(self.sa_file, 1)
        sa_row.addWidget(self.sa_browse)
        self.admin_email = QLineEdit()
        self.admin_email.setPlaceholderText("admin@yourcompany.com")
        # Admin: Microsoft app registration
        self.tenant = QLineEdit()
        self.tenant.setPlaceholderText("yourcompany.onmicrosoft.com or tenant ID")
        self.app_id = QLineEdit()
        self.app_id.setPlaceholderText("Application (client) ID")
        self.app_secret = QLineEdit()
        self.app_secret.setEchoMode(QLineEdit.Password)
        self.app_secret.setPlaceholderText("Client secret value")
        self.connect_btn = button("Connect", "shield", "test", "#ffffff")
        self.connect_btn.clicked.connect(self.admin_requested)
        connect_row = QHBoxLayout()
        connect_row.setSpacing(14)
        connect_row.addWidget(self.connect_btn)
        self.admin_label = label("", "muted", True)
        connect_row.addWidget(self.admin_label, 1)

        # Other providers: optional standards-based contacts/calendar/files
        self.carddav = QLineEdit()
        self.carddav.setPlaceholderText("https://contacts.example.com/ (optional)")
        self.caldav = QLineEdit()
        self.caldav.setPlaceholderText("https://calendar.example.com/ (optional)")
        self.webdav = QLineEdit()
        self.webdav.setPlaceholderText("https://files.example.com/remote.php/dav/files/me/ "
                                       "(optional)")

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
        grid.add("signin", "key", "Account", signin_row,
                 "Your browser opens to sign in. Mail Migrator never sees your password.")
        self.sa_hint = grid.add("sa_file", "key", "Service account key", sa_row,
                                "JSON key of a service account with domain-wide delegation.")
        grid.add("admin_email", "shield", "Admin email", self.admin_email,
                 "A Workspace super admin; used to list users and shared drives.")
        grid.add("tenant", "server", "Tenant", self.tenant)
        grid.add("app_id", "box", "Application ID", self.app_id)
        grid.add("app_secret", "key", "Client secret", self.app_secret,
                 "From your Microsoft Entra app registration (see the setup guide).")
        grid.add("connect", "wifi", "", connect_row)
        grid.add("user", "at", "Username", self.user, "Your full email address.")
        self.secret_label_row = grid.add(
            "secret", "key", "Password", secret_row,
            "Use your email password or an app password if required.")
        self.dav_title = label("Contacts, calendars & files (optional)", "h3")
        grid.add_widget(self.dav_title)
        self.dav_hint = label("Fill these in to also copy contacts, calendars or files from "
                              "this provider. The username and password above are used.",
                              "hint", True)
        grid.add_widget(self.dav_hint)
        grid.add("carddav", "users", "Contacts", self.carddav)
        grid.add("caldav", "calendar", "Calendars", self.caldav)
        grid.add("webdav", "folder", "Files", self.webdav)
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
        self.bulk_note = label("", "muted", True)
        il.addWidget(self.bulk_note)
        self.body.addWidget(inner)
        self.body.addStretch(1)

        self._last_provider = "custom"
        self._bulk = False
        self.signed_in = ""          # account shown after a successful sign-in / connect
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

    def _browse_key(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "Service account key", "",
                                              "JSON key (*.json);;All files (*)")
        if path:
            self.sa_file.setText(path)

    def _toggle_secret(self, on: bool) -> None:
        self.secret.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)
        self.eye.setIcon(t.icon("eye" if on else "eye-off", t.MUTED))

    def _invalidate(self, *_):
        if self.tested_ok:
            self.tested_ok = False
            self.status.set_state("idle", 'Settings changed. Click "Test Connection" to '
                                          "check them again.")

    def platform(self) -> str:
        return CLOUD_PLATFORM.get(self.provider.currentData(), "imap")

    def auth_mode(self) -> str:
        return self.auth.currentData() or AUTH_PASSWORD

    def uses_cloud_login(self) -> bool:
        return self.platform() != "imap" and self.auth_mode() in (AUTH_SIGNIN, AUTH_ADMIN)

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
        previous = self.auth.currentData()
        self.auth.blockSignals(True)
        self.auth.clear()
        for key, text in AUTH_FOR[self.platform()]:
            self.auth.addItem(text, key)
        self.auth.blockSignals(False)
        if previous and self.auth.findData(previous) >= 0:
            set_combo(self.auth, previous)
        from .cloud.dav import well_known
        dav = well_known(p.key)
        for field, edit in (("carddav", self.carddav), ("caldav", self.caldav),
                            ("webdav", self.webdav)):
            if dav.get(field) and not edit.text():
                edit.setText(dav[field])
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
        mode = self.auth_mode()
        platform = self.platform()
        oauth = mode == AUTH_OAUTH2
        row = self.grid.rows["secret"]
        row[1].setText("Access token" if oauth else "Password")
        self.secret.setPlaceholderText("Paste the OAuth2 access token" if oauth else "")
        if self.secret_label_row:
            self.secret_label_row.setText(
                "A token issued for this mailbox by your provider (OAuth2)." if oauth else
                "Use your email password or an app password if required.")
        brand = "Google" if platform == "google" else "Microsoft"
        self.signin_btn.setText(f"Sign in with {brand}")
        signin = platform != "imap" and mode == AUTH_SIGNIN
        admin = platform != "imap" and mode == AUTH_ADMIN
        self.grid.set_visible("signin", signin)
        for key in ("sa_file", "admin_email"):
            self.grid.set_visible(key, admin and platform == "google")
        for key in ("tenant", "app_id", "app_secret"):
            self.grid.set_visible(key, admin and platform == "microsoft")
        self.grid.set_visible("connect", admin)
        self.grid.set_visible("user", not self._bulk and not signin)
        self.grid.set_visible("secret", not self._bulk and not signin and not admin)
        user_row = self.grid.rows["user"]
        user_row[1].setText("Mailbox" if admin else "Username")
        dav = platform == "imap" and self.side == 1
        for key in ("carddav", "caldav", "webdav"):
            self.grid.set_visible(key, dav)
        self.dav_title.setVisible(dav)
        self.dav_hint.setVisible(dav)
        self._update_bulk_note()

    def _update_bulk_note(self) -> None:
        if not self._bulk:
            self.bulk_note.setVisible(False)
            return
        mode = self.auth_mode()
        if self.platform() != "imap" and mode == AUTH_SIGNIN:
            text = ("A bulk migration needs admin access on this side: choose the admin "
                    "option above, or app passwords / tokens per mailbox for email only.")
        elif self.platform() != "imap" and mode == AUTH_ADMIN:
            text = ("Admin access covers every mailbox. You'll list the mailboxes in the "
                    "Mailboxes step, or load them from your directory.")
        else:
            text = ("In a bulk migration, usernames and passwords are entered per mailbox in "
                    "the Mailboxes step, and every login is checked on the Review step.")
        self.bulk_note.setText(text)
        self.bulk_note.setVisible(True)

    def set_bulk(self, bulk: bool) -> None:
        self._bulk = bulk
        self._auth_changed()
        self.test_btn.setVisible(not bulk)
        self.status.setVisible(not bulk)

    def set_signed_in(self, who: str, ok: bool = True) -> None:
        self.signed_in = who if ok else ""
        target = self.admin_label if self.auth_mode() == AUTH_ADMIN else self.account_label
        target.setText(who)
        target.setStyleSheet(f"color: {t.GREEN if ok else t.RED};" if who else "")
        if ok and who and self.auth_mode() == AUTH_SIGNIN:
            self.user.setText(who.split(" ")[0])

    def set_signing_in(self, busy: bool) -> None:
        self.signin_btn.setText("Cancel sign-in" if busy else
                                f"Sign in with {'Google' if self.platform() == 'google' else 'Microsoft'}")
        self.connect_btn.setEnabled(not busy)
        if busy:
            self.account_label.setText("Waiting for you to finish signing in in your browser…")
            self.account_label.setStyleSheet("")

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
                      auth=self.auth_mode())

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

    def dav(self) -> dict:
        if self.platform() != "imap" or self.side != 1:
            return {}
        return {"carddav": self.carddav.text().strip(), "caldav": self.caldav.text().strip(),
                "webdav": self.webdav.text().strip()}

    def admin_settings(self) -> dict:
        return {"sa_file": self.sa_file.text().strip(),
                "admin_email": self.admin_email.text().strip(),
                "tenant": self.tenant.text().strip(), "app_id": self.app_id.text().strip()}

    def set_admin_settings(self, data: dict) -> None:
        self.sa_file.setText(data.get("sa_file", ""))
        self.admin_email.setText(data.get("admin_email", ""))
        self.tenant.setText(data.get("tenant", ""))
        self.app_id.setText(data.get("app_id", ""))

    def endpoint_text(self) -> str:
        s = self.server()
        p = PROVIDERS[s.provider]
        host = s.host or p.host or "(not set)"
        port = s.port or (993 if s.security == SECURITY_SSL else 143)
        sec = {SECURITY_SSL: "SSL/TLS", SECURITY_STARTTLS: "STARTTLS",
               SECURITY_NONE: "no encryption"}[s.security]
        return f"{host}:{port} (IMAP, {sec})"


# ---------------------------------------------------------------- what to migrate

DATA_KINDS = [
    ("mail", "mail", t.ACCENT, "Email",
     "Every folder and message, with read, flagged and answered status."),
    ("contacts", "users", t.PURPLE, "Contacts",
     "People, phone numbers, addresses, birthdays and contact groups."),
    ("calendars", "calendar", t.GREEN_DARK, "Calendars",
     "Events, recurring meetings and reminders — without re-sending invitations."),
    ("files", "folder", t.AMBER, "Files",
     "Google Drive ↔ OneDrive with folders; Google Docs, Sheets and Slides become Office "
     "files. Sharing is copied without notifications."),
    ("shared", "database", "#0ea5e9", "Shared drives",
     "Google shared drives ↔ SharePoint / Teams document libraries, with their members. "
     "Needs admin access on both sides."),
]


class DataChoice(QFrame):
    toggled = Signal(bool)

    def __init__(self, icon_name: str, color: str, title: str, text: str) -> None:
        super().__init__()
        self.setObjectName("inner")
        self.setCursor(Qt.PointingHandCursor)
        h = QHBoxLayout(self)
        h.setContentsMargins(20, 16, 20, 16)
        h.setSpacing(18)
        from .widgets import IconBadge
        h.addWidget(IconBadge(icon_name, color, 52), 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(4)
        col.addWidget(label(title, "h3"))
        col.addWidget(label(text, "muted", True))
        self.reason = label("", "hint", True)
        col.addWidget(self.reason)
        h.addLayout(col, 1)
        self.check = QCheckBox()
        self.check.toggled.connect(self.toggled)
        h.addWidget(self.check, 0, Qt.AlignVCenter)

    def mousePressEvent(self, _event) -> None:  # noqa: N802
        if self.check.isEnabled():
            self.check.toggle()

    def set_available(self, available: bool, reason: str) -> None:
        self.check.setEnabled(available)
        if not available:
            self.check.setChecked(False)
        self.reason.setText(reason)
        self.reason.setVisible(bool(reason))
        self.setStyleSheet("" if available else "#inner { background: #0d1426; }")


class DataPage(Page):
    changed = Signal()
    info = ("Choose what to migrate",
            "Email works with any provider. Contacts, calendars and files need Google "
            "Workspace or Microsoft 365 at the destination, and a supported source.")
    tips_title = "Good to know"
    tips = ["Everything is copied, never moved, unless you choose otherwise",
            "Re-running copies only what is new or changed",
            "Copied meetings never re-send invitations",
            "Sharing is copied without notification emails"]

    def __init__(self) -> None:
        super().__init__("check-square", "What would you like to migrate?",
                         "Pick the kinds of data to copy. You can run the migration again "
                         "later for anything you leave out.")
        self.choices: dict[str, DataChoice] = {}
        for key, icon_name, color, title, text in DATA_KINDS:
            choice = DataChoice(icon_name, color, title, text)
            choice.toggled.connect(lambda _on: self.changed.emit())
            self.choices[key] = choice
            self.body.addWidget(choice)
        self.choices["mail"].check.setChecked(True)
        self.body.addStretch(1)

    def selected(self) -> list[str]:
        return [k for k, c in self.choices.items() if c.check.isChecked() and c.check.isEnabled()]

    def set_selected(self, kinds: list[str]) -> None:
        for k, c in self.choices.items():
            c.check.blockSignals(True)
            c.check.setChecked(k in kinds and c.check.isEnabled())
            c.check.blockSignals(False)
        self.changed.emit()

    def set_availability(self, kind: str, available: bool, reason: str = "") -> None:
        choice = self.choices[kind]
        was = choice.check.isChecked()
        choice.set_available(available, reason)
        if was != choice.check.isChecked():
            self.changed.emit()


# ---------------------------------------------------------------- shared drives

class SharedDrivesPage(Page):
    load_requested = Signal()
    info = ("Choose the shared drives",
            "Pick which shared drives or document libraries to copy and where each one "
            "goes. New ones are created for you, and their members are added.")
    tips_title = "Shared drive tips"
    tips = ["Members are added without notification emails",
            "New Microsoft 365 group sites can take a few minutes to appear",
            "Use the address mapping in Options for renamed users or domains",
            "Re-running copies only new or changed files"]
    CREATE = "__create__"

    def __init__(self) -> None:
        super().__init__("database", "Shared drives",
                         "Copy team storage: Google shared drives and SharePoint / Teams "
                         "document libraries.", "#0ea5e9")
        inner, il = card("inner")
        il.setContentsMargins(24, 20, 24, 20)
        top = QHBoxLayout()
        self.count = label("Click “Load shared drives” to list them.", "muted", True)
        top.addWidget(self.count, 1)
        self.load_btn = button("Load shared drives", "refresh", obj="small")
        self.load_btn.clicked.connect(self.load_requested)
        top.addWidget(self.load_btn)
        il.addLayout(top)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Copy", "Source", "Destination", "New name"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for c in (1, 2, 3):
            hh.setSectionResizeMode(c, QHeaderView.Stretch)
        self.table.setMinimumHeight(300)
        il.addWidget(self.table, 1)
        self.body.addWidget(inner, 1)
        self._sources: list = []
        self._dests: list = []

    def set_drives(self, sources: list, dests: list, dest_label: str) -> None:
        self._sources, self._dests = sources, dests
        self.table.setRowCount(0)
        for d in sources:
            r = self.table.rowCount()
            self.table.insertRow(r)
            box = QCheckBox()
            box.setChecked(True)
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(12, 0, 0, 0)
            hl.addWidget(box)
            self.table.setCellWidget(r, 0, holder)
            item = QTableWidgetItem(d.name)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 1, item)
            combo = QComboBox()
            combo.addItem(f"Create new {dest_label}", self.CREATE)
            for dest in dests:
                combo.addItem(dest.name, dest.id)
            same = combo.findText(d.name)
            if same >= 0:
                combo.setCurrentIndex(same)
            self.table.setCellWidget(r, 2, combo)
            self.table.setItem(r, 3, QTableWidgetItem(d.name))
        self.count.setText(f"{len(sources)} found" if sources else
                           "No shared drives were found for this admin account.")

    def plans(self) -> list:
        from .cloud.migrate import DrivePlan
        out = []
        for r, d in enumerate(self._sources):
            box = self.table.cellWidget(r, 0).findChild(QCheckBox)
            if not box.isChecked():
                continue
            choice = self.table.cellWidget(r, 2).currentData()
            dest = next((x for x in self._dests if x.id == choice), None)
            name = (self.table.item(r, 3).text() or d.name).strip()
            out.append(DrivePlan(d, dest, name))
        return out


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
    directory_requested = Signal()

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
        self.directory_btn = button("Load users from directory", "users", obj="small")
        self.directory_btn.setToolTip("List every active user of the source organization. "
                                      "Destination addresses use the address mapping in "
                                      "Options.")
        self.directory_btn.setVisible(False)
        for b in (self.directory_btn, self.add_btn, self.remove_btn):
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
        self.directory_btn.clicked.connect(self.directory_requested)
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

    def set_sides(self, password1: bool, password2: bool, directory: bool) -> None:
        """Hide the password column of a side that uses admin access."""
        self.table.setColumnHidden(1, not password1)
        self.table.setColumnHidden(3, not password2)
        self.directory_btn.setVisible(directory)


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

        folders = OptionSection("folder", "Email folders",
                                "Choose how to handle mailbox folders during migration.")
        self.automap = QCheckBox("Match standard folders")
        self.automap.setChecked(True)
        folders.add_option(self.automap, "Map Sent, Drafts, Trash and Junk to the "
                                         "destination's own standard folders.")
        self.just_folders = QCheckBox("Copy folder structure only")
        folders.add_option(self.just_folders, "Recreate the folder structure without "
                                              "migrating messages.")
        il.addWidget(folders)
        div1 = divider()
        il.addWidget(div1)

        filters = OptionSection("mail", "Email filters",
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
        div2 = divider()
        il.addWidget(div2)

        sync = OptionSection("refresh", "Sync behavior",
                             "Mail Migrator always works this way — nothing to set.")
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
        self._mail_sections = [folders, div1, filters, div2, sync]

        # Contacts, calendars, files (shown when those are being migrated)
        self.cloud_box = QWidget()
        cb = QVBoxLayout(self.cloud_box)
        cb.setContentsMargins(0, 0, 0, 0)
        cb.setSpacing(10)
        cb.addWidget(divider())
        self.cal_section = OptionSection("calendar", "Calendars",
                                         "How events are copied.")
        cal_row = QHBoxLayout()
        self.cal_since_check = QCheckBox("Only events after")
        self.cal_since = QDateEdit(QDate.currentDate().addYears(-2))
        self.cal_since.setCalendarPopup(True)
        self.cal_since.setDisplayFormat("MMM d, yyyy")
        cal_row.addWidget(self.cal_since_check)
        cal_row.addWidget(self.cal_since)
        cal_row.addStretch(1)
        self.cal_section.add_layout(cal_row)
        self.cal_attendees = QCheckBox("Add attendees to copied meetings in Microsoft 365")
        self.cal_section.add_option(
            self.cal_attendees,
            "Off by default: Outlook would send every attendee a new invitation. When off, "
            "attendees are listed in the event notes instead. (Google never sends "
            "invitations for copied events.)")
        cb.addWidget(self.cal_section)
        cb.addWidget(divider())
        self.files_section = OptionSection("folder", "Files & sharing",
                                           "How files and folders are copied.")
        folder_row = QHBoxLayout()
        folder_row.addWidget(label("Put copied files in folder"))
        self.files_folder = QLineEdit()
        self.files_folder.setPlaceholderText("Top level (or e.g. “From Google Drive”)")
        folder_row.addWidget(self.files_folder, 1)
        self.files_section.add_layout(folder_row)
        self.convert = QCheckBox("Convert Word, Excel and PowerPoint files to Google Docs, "
                                 "Sheets and Slides")
        self.files_section.add_option(self.convert)
        limit_row = QHBoxLayout()
        limit_row.addWidget(label("Skip files larger than"))
        self.file_limit = QSpinBox()
        self.file_limit.setRange(0, 250000)
        self.file_limit.setSpecialValueText("No limit")
        self.file_limit.setSuffix(" MB")
        limit_row.addWidget(self.file_limit)
        limit_row.addStretch(1)
        self.files_section.add_layout(limit_row)
        self.copy_sharing = QCheckBox("Copy sharing and shared drive members")
        self.copy_sharing.setChecked(True)
        self.files_section.add_option(self.copy_sharing,
                                      "People are added without notification emails.")
        cb.addWidget(self.files_section)
        cb.addWidget(divider())
        self.map_section = OptionSection(
            "users", "People & addresses",
            "If addresses change in the move, map them so sharing, meeting attendees and "
            "shared drive members point at the new accounts.")
        from PySide6.QtWidgets import QPlainTextEdit
        self.user_map = QPlainTextEdit()
        self.user_map.setObjectName("field")
        self.user_map.setPlaceholderText("@old-company.com = @new-company.com\n"
                                         "jsmith@old-company.com = john.smith@new-company.com")
        self.user_map.setFixedHeight(84)
        self.map_section.add_option(self.user_map)
        cb.addWidget(self.map_section)
        il.addWidget(self.cloud_box)
        self.cloud_box.setVisible(False)

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
                               if self.range_msgs.isChecked() else None),
                "cloud": {"cal_since": (self.cal_since.date().toString(Qt.ISODate)
                                        if self.cal_since_check.isChecked() else None),
                          "cal_attendees": self.cal_attendees.isChecked(),
                          "files_folder": self.files_folder.text(),
                          "convert": self.convert.isChecked(),
                          "file_limit": self.file_limit.value(),
                          "copy_sharing": self.copy_sharing.isChecked(),
                          "user_map": self.user_map.toPlainText()}}

    def set_kinds(self, kinds: list[str], dest_platform: str) -> None:
        """Show only the option sections that apply to what is being migrated."""
        cal, files = "calendars" in kinds, ("files" in kinds or "shared" in kinds)
        self.cloud_box.setVisible(cal or files or "contacts" in kinds)
        self.cal_section.setVisible(cal)
        self.cal_attendees.setVisible(dest_platform == "microsoft")
        self.files_section.setVisible(files)
        self.convert.setVisible(dest_platform == "google")
        self.mail_only_widgets_visible("mail" in kinds)

    def mail_only_widgets_visible(self, on: bool) -> None:
        for w in self._mail_sections:
            w.setVisible(on)

    def cloud_options(self, temp_dir: str | None = None):
        from datetime import datetime as _dt, timezone as _tz
        from .cloud.migrate import CloudOptions
        from .cloud.state import UserMap
        since = None
        if self.cal_since_check.isChecked():
            d = self.cal_since.date().toPython()
            since = _dt(d.year, d.month, d.day, tzinfo=_tz.utc)
        return CloudOptions(since=since,
                            calendar_attendees=(True if self.cal_attendees.isChecked()
                                                else None),
                            convert_documents=self.convert.isChecked(),
                            copy_permissions=self.copy_sharing.isChecked(),
                            max_file_mb=self.file_limit.value(),
                            files_folder=self.files_folder.text().strip(),
                            user_map=UserMap(self.user_map.toPlainText()), temp_dir=temp_dir)

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
        cloud = extras.get("cloud") or {}
        if cloud:
            if cloud.get("cal_since"):
                self.cal_since_check.setChecked(True)
                self.cal_since.setDate(QDate.fromString(cloud["cal_since"], Qt.ISODate))
            self.cal_attendees.setChecked(bool(cloud.get("cal_attendees")))
            self.files_folder.setText(cloud.get("files_folder", ""))
            self.convert.setChecked(bool(cloud.get("convert")))
            self.file_limit.setValue(int(cloud.get("file_limit") or 0))
            self.copy_sharing.setChecked(cloud.get("copy_sharing", True) is not False)
            self.user_map.setPlainText(cloud.get("user_map", ""))
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

    def cloud_summary(self, kinds: list[str], dest_platform: str) -> list[str]:
        lines = []
        if "calendars" in kinds:
            if self.cal_since_check.isChecked():
                lines.append("Calendar events after " +
                             self.cal_since.date().toString("MMM d, yyyy"))
            if dest_platform == "microsoft" and self.cal_attendees.isChecked():
                lines.append("⚠ Attendees added to meetings — Outlook will send invitations")
            else:
                lines.append("Meetings copied without sending invitations")
        if "files" in kinds or "shared" in kinds:
            if self.files_folder.text().strip():
                lines.append(f"Files go in “{self.files_folder.text().strip()}”")
            if dest_platform == "google" and self.convert.isChecked():
                lines.append("Office files converted to Google Docs, Sheets and Slides")
            if self.file_limit.value():
                lines.append(f"Skip files larger than {self.file_limit.value():,} MB")
            lines.append("Sharing copied without notifications" if self.copy_sharing.isChecked()
                         else "Sharing not copied")
        rules = [x for x in self.user_map.toPlainText().splitlines() if x.strip()]
        if rules:
            lines.append(f"{len(rules)} address mapping rule{'s' if len(rules) != 1 else ''}")
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
        self.data_sec = ReviewSection("check-square", t.AMBER, "What to Migrate")
        self.opt_sec = ReviewSection("settings", t.GREEN_DARK, "Selected Options")
        for sec, key in ((self.type_sec, "type"), (self.src_sec, "source"),
                         (self.dst_sec, "destination"), (self.data_sec, "data"),
                         (self.mbx_sec, "mailboxes"),
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
        tl.addWidget(label("Progress by mailbox", "h3"))
        self.table = QTableWidget(0, 2)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setMinimumHeight(180)
        tl.addWidget(self.table)
        self.body.addWidget(self.table_frame, 1)
        self.body.addStretch(1)
        self.kinds: list[str] = []

    COLUMN_NAMES = {"mail": "Email", "contacts": "Contacts", "calendars": "Calendars",
                    "files": "Files", "shared": "Shared drive"}

    def reset(self, mode: str, accounts: list[Account], kinds: list[str] | None = None,
              shared: list | None = None) -> None:
        shared = shared or []
        kinds = [k for k in (kinds or ["mail"]) if k != "shared"]
        if shared and "files" not in kinds:
            kinds.append("files")   # shared drive rows report in the Files column
        only_mail = kinds == ["mail"] and not shared
        noun = "mail" if only_mail else "data"
        titles = {"dry": ("Simulating your migration…",
                          "Nothing is copied or changed: this shows what would happen."),
                  "migrate": (f"Migrating your {noun}…",
                              "Everything selected is being copied from the source to the "
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
        self.kinds = kinds
        headers = ["Source", "Destination"] + [self.COLUMN_NAMES[k] for k in kinds]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        hh = self.table.horizontalHeader()
        for c in range(len(headers)):
            hh.setSectionResizeMode(c, QHeaderView.Stretch)
        self.table.setRowCount(0)
        for a in accounts:
            self._add_row(a.user1, a.user2, ["Waiting"] * len(kinds))
        self.shared_offset = self.table.rowCount()
        for plan in shared:
            dest = plan.dest.name if plan.dest else f"{plan.dest_name} (new)"
            self._add_row("Shared: " + plan.source.name, dest,
                          ["Waiting" if k == "files" else "" for k in kinds])
        self.table_frame.setVisible(len(accounts) > 1 or len(kinds) > 1 or bool(shared))

    def _add_row(self, a: str, b: str, cells: list[str]) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c, text in enumerate([a, b] + cells):
            item = QTableWidgetItem(text)
            item.setToolTip(text)
            self.table.setItem(r, c, item)

    def set_cell(self, row: int, kind: str, text: str, color: str = t.TEXT) -> None:
        if kind not in self.kinds or not 0 <= row < self.table.rowCount():
            return
        item = self.table.item(row, 2 + self.kinds.index(kind))
        item.setText(text)
        item.setForeground(QColor(color))
        item.setToolTip(text)

    def set_shared(self, index: int, text: str, color: str = t.TEXT) -> None:
        row = self.shared_offset + index
        if 0 <= row < self.table.rowCount():
            col = 2 + self.kinds.index("files")
            item = self.table.item(row, col)
            item.setText(text)
            item.setForeground(QColor(color))
            item.setToolTip(text)

    def set_row(self, index: int, text: str, color: str = t.TEXT) -> None:
        """Email status (kept for the mail runner)."""
        self.set_cell(index, "mail", text, color)
