# Mail Migrator

A desktop app for Windows, macOS and Linux that copies email from one IMAP
account to another. It is a graphical front end for
[imapsync](https://imapsync.lamiral.info/) (the Perl script one folder up),
and ships with a standalone copy of imapsync so users do not need Perl.

## Using it

The app walks you through a short wizard:

1. **Migration Type** — *Single Mailbox* (one account) or *Bulk* (many accounts
   between the same two servers).
2. **Source** — choose the provider (Gmail, Microsoft 365, iCloud, Yahoo, …) or
   *Other* and type the IMAP server, then sign in. **Test Connection** checks
   the login on its own.
3. **Destination** — the same for the account that should receive the mail.
4. **Mailboxes** (bulk only) — type one row per mailbox, or import a CSV file
   with lines like `user1;password1;user2;password2` (*CSV template* saves an
   example).
5. **Options** — standard-folder matching, date range, message size limit,
   and advanced settings. "Run a dry-run simulation first" is on by default.
6. **Review & Start** — check everything, click **Estimate** to count the
   messages, folders and size on the source, then **Simulate** or
   **Start Migration**.

You can stop at any time and start again later: imapsync never copies a
message twice, so re-running only copies what is new.

The sidebar also has **Saved Presets** (server and option settings, never
passwords), **Migration History**, **Logs** (every imapsync log, viewable in the
app), **Settings** and **Help & Support** (provider guides).

**Provider notes**

* Gmail, iCloud, Yahoo, AOL, Fastmail, Yandex: use an *app password*, not
  your normal password.
* Microsoft 365 / Outlook.com: Microsoft blocks plain passwords for IMAP.
  Choose *OAuth2 access token* and paste a token for the mailbox.

**Safety**

* Passwords are passed to imapsync through environment variables, never on
  the command line, and are never written to profiles or logs.
* Server certificates are verified (imapsync on its own does not do this).
  Untick *Verify server certificates* in Advanced options only for a
  server with a self-signed certificate you trust.
* Nothing is ever deleted unless you tick one of the two delete options in
  Advanced options; both ask for confirmation.
* Logs of every run are kept: *Open log folder* in the app.

## Downloads and first launch

Release builds are produced by GitHub Actions (`.github/workflows/build-apps.yml`):

| Platform | File | Notes |
| --- | --- | --- |
| Windows 10/11 x64 | `MailMigrator-*-windows-x64.zip` | Unzip, run `Mail Migrator.exe`. SmartScreen may warn because the app is not signed: *More info › Run anyway*. |
| macOS 11+ Apple Silicon | `MailMigrator-*-macos-arm64.dmg` | Drag to Applications. The app is not notarized: the first time, right-click it › *Open*. |
| macOS 11+ Intel | `MailMigrator-*-macos-x64.dmg` | Same as above. |
| Linux x64 (glibc 2.35+, e.g. Ubuntu 22.04+, Debian 12+, Fedora 36+) | `MailMigrator-*-linux-x64.tar.gz` | Extract, run `MailMigrator/Mail Migrator`. Needs OpenSSL 3 (standard on these distributions). |

Push a tag like `v1.0.0` to publish all of them as a GitHub release.

## Running from source

```sh
# 1. imapsync's Perl dependencies (macOS: brew install cpanminus openssl@3)
cd imapsync-master
cpanm --installdeps .
# 2. The GUI
cd gui
python3 -m pip install -r requirements.txt
python3 run_app.py
```

The app looks for imapsync in this order: `$MAILMIGRATOR_IMAPSYNC`, the copy
bundled in a packaged app, `../imapsync` (run with `perl`), then `imapsync` on
the `PATH` (for example from `brew install imapsync` or `apt install imapsync`).

## Building a release on your own machine

```sh
cd imapsync-master
cpanm --installdeps --with-develop .         # adds PAR::Packer
gui/scripts/macos-static-openssl.sh          # macOS only, see the script
cd gui
python3 -m pip install -r requirements-build.txt
python3 -m pytest tests
python3 build.py all                          # → gui/release/
```

`build.py` has three steps that can also be run one by one:

* `imapsync` — packs `../imapsync` and its modules into `imapsync-bin/imapsync[.exe]`
  with PAR::Packer, then checks the result runs without Perl on the `PATH`.
* `app` — builds the GUI with PyInstaller into `dist/` and copies the
  imapsync binary inside.
* `package` — makes the `.zip` (Windows), `.dmg` (macOS) or `.tar.gz` (Linux).

Each platform must be built on that platform; the GitHub workflow does all
four (Windows, macOS arm64 and x64, Linux) on every push.

## Code layout

| File | What it does |
| --- | --- |
| `mailmigrator/engine.py` | Turns the form into an imapsync command line; finds imapsync. No Qt, unit tested. |
| `mailmigrator/providers.py` | Server settings for common mail providers. |
| `mailmigrator/progress.py` | Reads imapsync's output to drive the progress bar and summary. |
| `mailmigrator/batch.py` | CSV import/export of mailbox lists. |
| `mailmigrator/runner.py` | Runs imapsync for each mailbox in turn (Qt `QProcess`). |
| `mailmigrator/store.py` | Saved presets and migration history (JSON, no passwords). |
| `mailmigrator/theme.py` | Dark theme colours, line icons and the Qt style sheet. |
| `mailmigrator/widgets.py` | Stepper, option cards, toggles, log panel and other building blocks. |
| `mailmigrator/pages.py` | The wizard steps and the progress screen. |
| `mailmigrator/sections.py` | Presets, history, logs, settings and help screens. |
| `mailmigrator/app.py` | The main window: sidebar, wizard flow, runs. |
| `tests/` | `pytest` unit tests. |
