# Mail Migrator

**Move email, contacts, calendars and files between Google Workspace,
Microsoft 365 and any other mail provider, with a desktop app for Windows,
macOS and Linux.**

Mail Migrator copies every folder and message from one account to another,
whether you're moving one mailbox or hundreds. With Google Workspace and
Microsoft 365 it also moves **contacts, calendars, Drive/OneDrive files,
sharing and shared drives**, signing in with Google or Microsoft, or with
admin access to move a whole organization without anyone's password. It's a graphical front end for
[imapsync](https://imapsync.lamiral.info/), the long-trusted migration engine
by Gilles Lamiral, and it bundles imapsync, so there's nothing else to install.

![Choosing a single or bulk migration](docs/screenshots/1-migration-type.png)

## Download

Get the latest version from the
[**Releases page**](../../releases/latest):

| Platform | File | First launch |
| --- | --- | --- |
| **Windows** 10/11 (64-bit) | `MailMigrator-*-windows-x64.zip` | Unzip, then run `Mail Migrator.exe`. If SmartScreen warns, choose *More info → Run anyway* (the app isn't code-signed). |
| **macOS** Apple Silicon (M1 and later) | `MailMigrator-*-macos-arm64.dmg` | Drag *Mail Migrator* to Applications. The first time, right-click it and choose *Open* (the app isn't notarized). |
| **macOS** Intel | `MailMigrator-*-macos-x64.dmg` | Same as above. |
| **Linux** x64 (Ubuntu 22.04+, Debian 12+, Fedora 36+) | `MailMigrator-*-linux-x64.tar.gz` | Extract, then run `MailMigrator/Mail Migrator`. |

## What it can move

| From → To | Email | Contacts | Calendars | Files & sharing | Shared drives |
| --- | :-: | :-: | :-: | :-: | :-: |
| Google Workspace → Microsoft 365 | ✓ | ✓ | ✓ | ✓ | ✓ (admin) |
| Microsoft 365 → Google Workspace | ✓ | ✓ | ✓ | ✓ | ✓ (admin) |
| Google → Google, Microsoft → Microsoft | ✓ | ✓ | ✓ | ✓ | ✓ (admin) |
| iCloud, Fastmail, Zoho, Nextcloud, … → Google or Microsoft | ✓ | ✓ CardDAV | ✓ CalDAV | ✓ WebDAV | — |
| Any IMAP server → any IMAP server | ✓ | — | — | — | — |

Copied meetings never send invitations, and copied sharing never sends
notification emails. Setting up the Google and Microsoft side is described in
the [setup guide](docs/google-microsoft-setup.md).

## How it works

A short wizard takes you from start to finish:

1. **Migration type:** one mailbox, or many mailboxes in bulk (type them in
   or import a CSV file).
2. **Source:** where your data is now. For Google Workspace or Microsoft 365,
   click **Sign in** (or connect with admin access). For other providers enter
   the IMAP server, plus optional contacts/calendar/file addresses.
3. **Destination:** where it should go.
4. **What to migrate:** email, contacts, calendars, files, shared drives.
5. **Options:** folder matching, date ranges, size limits, converting
   documents, copying sharing, and mapping old addresses to new ones.
6. **Review & Start:** see everything at a glance, **Estimate** the number of
   messages and the size, **Simulate** first, then **Start Migration**.

| Connect a server | Choose options |
| --- | --- |
| ![Source server step](docs/screenshots/2-source.png) | ![Options step](docs/screenshots/3-options.png) |
| **Review before starting** | **Live progress and results** |
| ![Review step](docs/screenshots/4-review.png) | ![Migration finished](docs/screenshots/5-finished.png) |

### Safe by default

- **Nothing is deleted** unless you turn on one of the two delete options, and
  both ask for confirmation.
- **No duplicates:** messages already at the destination are skipped, so you
  can stop at any time and run the migration again later to pick up only
  what's new.
- **Passwords are never saved.** They're handed to imapsync privately, never
  on the command line, and never written to presets, history or logs.
- **Server certificates are verified.** imapsync doesn't check them by
  default; Mail Migrator does, which protects your passwords from
  interception.
- **Full logs** of every run are kept and can be viewed in the app.

### Provider tips

- **Gmail, iCloud, Yahoo, AOL, Fastmail, Yandex:** use an *app password*, not
  your normal password.
- **Microsoft 365 / Outlook.com:** Microsoft no longer accepts plain passwords
  for IMAP. Choose *OAuth2 access token* and paste a token for the mailbox.

The app's **Help & Support** screen links to each provider's instructions.

## Bulk migrations

Choose **Bulk Migration**, then fill in the mailbox table or import a CSV file
with one mailbox per line:

```
user1;password1;user2;password2
alice@old-domain.com;old-password;alice@new-domain.com;new-password
```

Mailboxes are migrated one after the other, and each gets its own status and
log. **Run checks** on the review step signs in to every mailbox before you
start.

![Bulk migration review](docs/screenshots/6-bulk-review.png)

## For developers

The desktop app lives in [`gui/`](gui/): Python with Qt (PySide6). The
imapsync engine is the [`imapsync`](imapsync) Perl script at the top of this
repository, unchanged from upstream release 2.314.

```sh
# Run from source
cpanm --installdeps .                 # imapsync's Perl modules (see cpanfile)
cd gui
python3 -m pip install -r requirements.txt
python3 run_app.py
```

Builds for every platform run automatically in GitHub Actions
([`.github/workflows/build-apps.yml`](.github/workflows/build-apps.yml)), and
pushing a tag like `v2.0.0` publishes a release. To build on your own machine,
see [`gui/README.md`](gui/README.md).

### What changed compared with upstream imapsync

- A cross-platform desktop app (`gui/`).
- Standalone builds of imapsync for Windows, macOS (including Apple Silicon,
  which upstream doesn't ship) and Linux, with OpenSSL and certificate
  authorities bundled.
- A `cpanfile` listing every Perl dependency, for one-command installs.
- GitHub Actions workflows brought up to date.

Not included: imapsync's `oauth2/` helper scripts and `X/oauth2_example.html`,
because they contain the imapsync author's own OAuth app credentials. Get them
from [upstream imapsync](https://github.com/imapsync/imapsync) if you need them.

imapsync's own documentation is still here: [README](README), [FAQ.d/](FAQ.d)
and [INSTALL.d/](INSTALL.d).

## Credits and license

imapsync is written and maintained by
[Gilles Lamiral](https://imapsync.lamiral.info/) and released under the
[NOLIMIT Public License](LICENSE). If imapsync saves you time, consider
[supporting its author](https://imapsync.lamiral.info/#BUY).

Mail Migrator, the desktop app in `gui/`, is released under the same license.
