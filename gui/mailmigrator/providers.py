"""Well known mail providers and their IMAP settings."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    host: str = ""
    port: int = 0
    security: str = "ssl"
    # imapsync has built-in tuning for some providers: --gmail1, --office1...
    flag: str = ""
    hint: str = ""


_LIST = [
    Provider("custom", "Other / custom server",
             hint="Enter the IMAP server address given by your mail host."),
    Provider("gmail", "Gmail / Google Workspace", host="imap.gmail.com", flag="gmail",
             hint="Turn on IMAP in Gmail settings and use an App Password "
                  "(Google Account > Security > App passwords), or an OAuth2 access token."),
    Provider("office365", "Microsoft 365 / Outlook.com", host="outlook.office365.com",
             flag="office",
             hint="Microsoft no longer accepts plain passwords for IMAP. "
                  "Choose 'OAuth2 access token' and paste a token for the mailbox."),
    Provider("exchange", "Microsoft Exchange (on-premises)", flag="exchange",
             hint="Enter your Exchange server address. IMAP must be enabled on the server."),
    Provider("icloud", "iCloud Mail", host="imap.mail.me.com",
             hint="Use an app-specific password from appleid.apple.com."),
    Provider("yahoo", "Yahoo Mail", host="imap.mail.yahoo.com",
             hint="Generate an app password in Yahoo Account Security."),
    Provider("aol", "AOL Mail", host="imap.aol.com",
             hint="Generate an app password in AOL Account Security."),
    Provider("zoho", "Zoho Mail", host="imap.zoho.com",
             hint="Enable IMAP access in Zoho Mail settings. EU accounts use imap.zoho.eu."),
    Provider("fastmail", "Fastmail", host="imap.fastmail.com",
             hint="Create an app password in Fastmail Settings > Privacy & Security."),
    Provider("godaddy", "GoDaddy Workspace Email", host="imap.secureserver.net",
             hint="Use your full email address as username."),
    Provider("ionos", "IONOS (1&1)", host="imap.ionos.com"),
    Provider("gmx", "GMX Mail", host="imap.gmx.com",
             hint="Enable IMAP access in GMX settings first."),
    Provider("yandex", "Yandex Mail", host="imap.yandex.com",
             hint="Use an app password from Yandex ID settings."),
    Provider("proton", "Proton Mail (Bridge)", host="127.0.0.1", port=1143,
             security="starttls",
             hint="Proton Mail Bridge must be running. Use the username and "
                  "password shown in the Bridge app."),
]

PROVIDERS: dict[str, Provider] = {p.key: p for p in _LIST}
PROVIDER_ORDER: list[str] = [p.key for p in _LIST]
