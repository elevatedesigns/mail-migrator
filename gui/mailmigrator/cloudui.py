"""Google Workspace / Microsoft 365 parts of the main window.

Kept apart from app.py: signing in, admin connections, which data types are
available, loading users and shared drives, and running contacts, calendars,
files and shared drives in a background thread.
"""

from __future__ import annotations

import json
import os
import threading
import traceback

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QMessageBox

from . import theme as t
from .cloud import auth
from .cloud.config import app_ids
from .cloud.http import ApiError, Stopped
from .cloud.models import Stats
from .cloud.services import (KINDS, LABELS, Side, check_access, directory_users, list_shared,
                             run_mailbox, run_shared_drives)
from .engine import AUTH_ADMIN, AUTH_SIGNIN, Account

APP_NAME = "Mail Migrator"


class BgTask(QObject):
    """Run a function in a thread; deliver (result, error) back on the GUI thread."""

    done = Signal(object, object)
    _live: set = set()

    def __init__(self, fn, on_done, parent=None) -> None:
        super().__init__(parent)
        self.fn = fn
        self.done.connect(on_done)
        BgTask._live.add(self)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            result, error = self.fn(), None
        except Exception as exc:  # delivered to the GUI as a message
            result, error = None, exc
        try:
            self.done.emit(result, error)
        finally:
            BgTask._live.discard(self)


class CloudWorker(QThread):
    log = Signal(str, str)
    progress = Signal(int, str, int, int, str)        # row, kind, done, total, message
    mailbox_done = Signal(int, object)                # row, {kind: Stats}
    shared_done = Signal(int, object)                 # plan index, Stats

    def __init__(self, src: Side, dst: Side, accounts: list[Account], kinds: list[str],
                 plans: list, opts, state_dir: str, parent=None) -> None:
        super().__init__(parent)
        self.src, self.dst = src, dst
        self.accounts, self.kinds, self.plans = accounts, kinds, plans
        self.opts, self.state_dir = opts, state_dir
        self.stop_event = threading.Event()
        self.stopped = False

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:  # noqa: D401 (QThread API)
        try:
            cloud_kinds = [k for k in self.kinds if k in KINDS]
            if cloud_kinds:
                for row, a in enumerate(self.accounts):
                    if self.stop_event.is_set():
                        raise Stopped()
                    self.log.emit(f"{a.user1} → {a.user2}", "heading")
                    results = run_mailbox(
                        self.src, self.dst, a.user1, a.user2, cloud_kinds, self.opts,
                        self.state_dir, self.stop_event,
                        lambda kind, done, total, msg, r=row:
                            self.progress.emit(r, kind, done, total, msg),
                        lambda msg, level: self.log.emit(msg, level))
                    self.mailbox_done.emit(row, results)
            if self.plans:
                for i, plan in enumerate(self.plans):
                    if self.stop_event.is_set():
                        raise Stopped()
                    res = run_shared_drives(
                        self.src, self.dst, [plan], self.opts, self.state_dir, self.stop_event,
                        lambda kind, done, total, msg, i=i:
                            self.progress.emit(-1 - i, "shared", done, total, msg),
                        lambda msg, level: self.log.emit(msg, level))
                    self.shared_done.emit(i, res.get(plan.source.id, Stats()))
        except Stopped:
            self.stopped = True
            self.log.emit("Stopped.", "warning")
        except Exception as exc:  # pragma: no cover - safety net
            self.log.emit(f"Unexpected error: {exc}", "error")
            self.log.emit(traceback.format_exc(), "error")


class CloudMixin:
    """Mixed into MainWindow."""

    # -------------------------------------------------------------- app IDs

    def _app_overrides(self) -> dict:
        raw = self.settings.value("app_ids", "") or ""
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return {}

    def _apps_changed(self, values: dict) -> None:
        self.settings.setValue("app_ids", json.dumps(values))
        self.log_card.add("Saved Google & Microsoft app settings.", "ok")

    # -------------------------------------------------------------- sign-in

    def _page(self, side: int):
        return self.src_page if side == 1 else self.dst_page

    def _sign_in(self, side: int) -> None:
        page = self._page(side)
        cancel = self._signin_cancel.get(side)
        if cancel is not None:          # the button reads "Cancel sign-in"
            cancel.set()
            return
        ids = app_ids(self._app_overrides())
        platform = page.platform()
        cancel = threading.Event()
        self._signin_cancel[side] = cancel
        page.set_signing_in(True)
        hint = page.user.text().strip()
        self.log_card.add(f"Opening your browser to sign in to "
                          f"{'Google' if platform == 'google' else 'Microsoft'}…", "heading")

        def work():
            if platform == "google":
                return auth.GoogleUser.sign_in(ids.google_client_id, ids.google_client_secret,
                                               hint, cancel)
            return auth.MicrosoftUser.sign_in(ids.microsoft_client_id, ids.microsoft_tenant,
                                              hint, cancel)

        def done(cred, error):
            self._signin_cancel.pop(side, None)
            page.set_signing_in(False)
            if error is not None:
                if isinstance(error, Stopped):
                    page.set_signed_in("Sign-in cancelled.", ok=False)
                    return
                page.set_signed_in(str(error), ok=False)
                self.log_card.add(f"Sign-in failed: {error}", "error")
                return
            self.creds[side] = cred
            who = cred.email or "(account)"
            page.set_signed_in(f"{who} — signed in")
            self.log_card.add(f"Signed in as {who}", "ok")
            self._reset_checks()
            self._update_availability()

        BgTask(work, done, self)

    def _admin_connect(self, side: int) -> None:
        page = self._page(side)
        platform = page.platform()
        page.admin_label.setText("Connecting…")
        page.admin_label.setStyleSheet("")
        if platform == "google":
            key_path, admin = page.sa_file.text().strip(), page.admin_email.text().strip()

            def work():
                if not key_path or not admin:
                    raise auth.AuthError("Choose the service account key and enter an "
                                         "admin email.")
                cred = auth.GoogleServiceAccount.from_file(key_path, admin)
                return cred, check_access(Side("google", cred), admin, [])
        else:
            tenant, app_id = page.tenant.text().strip(), page.app_id.text().strip()
            secret = page.app_secret.text()

            def work():
                cred = auth.MicrosoftApp(tenant, app_id, secret)
                cred.token("graph")
                users = directory_users(Side("microsoft", cred))
                return cred, f"{tenant} — admin access ({len(users):,} users visible)"

        def done(result, error):
            if error is not None:
                page.set_signed_in(f"Could not connect: {error}", ok=False)
                self.log_card.add(f"Admin connection failed: {error}", "error")
                return
            cred, description = result
            self.creds[side] = cred
            page.set_signed_in(description)
            self.log_card.add(f"Connected: {description}", "ok")
            self._reset_checks()
            self._update_availability()

        BgTask(work, done, self)

    # -------------------------------------------------------------- sides

    def _side(self, n: int) -> Side:
        page = self._page(n)
        platform = page.platform()
        if platform != "imap" and page.auth_mode() in (AUTH_SIGNIN, AUTH_ADMIN):
            cred = self.creds.get(n)
            if cred and cred.admin != (page.auth_mode() == AUTH_ADMIN):
                cred = None
            return Side(platform, cred)
        dav = page.dav()
        return Side("dav", None, dav.get("carddav", ""), dav.get("caldav", ""),
                    dav.get("webdav", ""), page.secret.text())

    def _token_provider(self, n: int, user: str):
        cred = self._side(n).cred
        if cred is None:
            return ""
        acting = cred.for_user(user) if cred.admin else cred
        resource = auth.imap_resource(acting)
        return lambda: acting.token(resource)

    def _update_availability(self, *_args) -> None:
        if not hasattr(self, "data_page"):
            return
        src = self._side(1)
        sp, dp = self.src_page, self.dst_page
        dst_cloud = dp.platform() != "imap"
        dst_login = dp.uses_cloud_login()
        src_cloud = sp.platform() != "imap"
        src_login = sp.uses_cloud_login()

        def reason(kind: str) -> tuple[bool, str]:
            if not dst_cloud:
                return False, "Needs Google Workspace or Microsoft 365 as the destination."
            if not dst_login:
                return False, ("Choose “Sign in” or admin access for the destination "
                               "(passwords only work for email).")
            if src_cloud and not src_login:
                return False, ("Choose “Sign in” or admin access for the source "
                               "(passwords only work for email).")
            if not src_cloud:
                url = {"contacts": sp.carddav, "calendars": sp.caldav,
                       "files": sp.webdav}[kind].text().strip()
                if not url:
                    what = {"contacts": "Contacts (CardDAV)", "calendars": "Calendars (CalDAV)",
                            "files": "Files (WebDAV)"}[kind]
                    return False, f"Add the source's {what} address on the Source step."
            return True, ""

        for kind in ("contacts", "calendars", "files"):
            self.data_page.set_availability(kind, *reason(kind))
        shared_ok = src_cloud and dst_cloud and sp.auth_mode() == AUTH_ADMIN and \
            dp.auth_mode() == AUTH_ADMIN
        self.data_page.set_availability(
            "shared", shared_ok,
            "" if shared_ok else "Needs admin access on both Google / Microsoft sides.")
        self.mbx_page.set_sides(
            password1=not src_login, password2=not dst_login,
            directory=src_cloud and sp.auth_mode() == AUTH_ADMIN and src.cred is not None)

    # -------------------------------------------------------------- loading

    def _load_directory(self) -> None:
        side = self._side(1)
        if not side.cred or not side.cred.admin:
            QMessageBox.information(self, APP_NAME, "Connect the source with admin access "
                                                    "first.")
            return
        umap_text = self.opt_page.user_map.toPlainText()
        self.log_card.add("Loading users from the source directory…", "heading")

        def done(users, error):
            if error is not None:
                self.log_card.add(f"Could not list users: {error}", "error")
                QMessageBox.warning(self, APP_NAME, f"Could not list users:\n{error}")
                return
            from .cloud.state import UserMap
            umap = UserMap(umap_text)
            accounts = [Account(email, "", umap.map(email), "") for email, _ in users]
            self.mbx_page.set_accounts(accounts)
            note = "" if umap else " Add an address mapping in Options if addresses change."
            self.log_card.add(f"Loaded {len(accounts):,} users.{note}", "ok")

        BgTask(lambda: directory_users(side), done, self)

    def _load_shared(self) -> None:
        src, dst = self._side(1), self._side(2)
        if not (src.cred and dst.cred and src.cred.admin and dst.cred.admin):
            QMessageBox.information(self, APP_NAME, "Connect both sides with admin access "
                                                    "first.")
            return
        self.shared_page.count.setText("Loading…")

        def done(result, error):
            if error is not None:
                self.shared_page.count.setText(f"Could not load: {error}")
                return
            sources, dests = result
            label = "shared drive" if dst.platform == "google" else "Microsoft 365 group site"
            self.shared_page.set_drives(sources, dests, label)

        BgTask(lambda: (list_shared(src), list_shared(dst)), done, self)

    # -------------------------------------------------------------- run

    def _cloud_kinds(self) -> list[str]:
        return [k for k in self.data_page.selected() if k in KINDS]

    def _start_cloud_phase(self, kind: str) -> bool:
        """Start contacts/calendars/files/shared drives. False if nothing to do."""
        kinds = self.data_page.selected()
        plans = self.shared_page.plans() if "shared" in kinds else []
        if not [k for k in kinds if k in KINDS] and not plans:
            return False
        opts = self.opt_page.cloud_options(os.path.join(self.workdir, "tmp"))
        opts.dry_run = kind == "dry"
        os.makedirs(opts.temp_dir, exist_ok=True)
        worker = CloudWorker(self._side(1), self._side(2), self._accounts_running, kinds,
                             plans, opts, os.path.join(self.workdir, "state"), self)
        worker.log.connect(self._cloud_log)
        worker.progress.connect(self._cloud_progress)
        worker.mailbox_done.connect(self._cloud_mailbox_done)
        worker.shared_done.connect(self._cloud_shared_done)
        worker.finished.connect(lambda: self._cloud_finished(kind))
        self.cloud_worker = worker
        self._cloud_stats: list[Stats] = []
        for row in range(len(self._accounts_running)):
            for k in kinds:
                if k in KINDS:
                    self.run_page.set_cell(row, k, "Waiting", t.MUTED)
        self.run_page.bar.setRange(0, 0)
        worker.start()
        self._update_footer()
        return True

    def _cloud_log(self, message: str, level: str) -> None:
        for line in message.splitlines() or [""]:
            self.log_card.add(line, level)

    def _cloud_progress(self, row: int, kind: str, done: int, total: int, msg: str) -> None:
        self.run_page.detail.setText(msg)
        if row >= 0:
            self.run_page.set_cell(row, kind, "Running…", t.AMBER)
            n = len(self._accounts_running)
            a = self._accounts_running[row]
            self.run_page.status.setText(f"{LABELS.get(kind, kind)}: {a.user1}" if n == 1 else
                                         f"{LABELS.get(kind, kind)} · mailbox {row + 1} of {n}")
        else:
            self.run_page.set_shared(-1 - row, "Running…", t.AMBER)
            self.run_page.status.setText("Shared drives")
        if total:
            if self.run_page.bar.maximum() == 0:
                self.run_page.bar.setRange(0, 1000)
            self.run_page.bar.setValue(int(min(1.0, done / total) * 1000))

    def _stats_text(self, s: Stats, noun: str, dry: bool) -> tuple[str, str]:
        text = s.summary(noun)
        if dry:
            text = text.replace("copied", "would be copied")
        if s.failed:
            return text, t.RED
        return text, t.GREEN

    def _cloud_mailbox_done(self, row: int, results: dict) -> None:
        dry = self.run_kind == "dry"
        for kind, stats in results.items():
            noun = {"contacts": "contacts", "calendars": "events", "files": "files"}[kind]
            text, color = self._stats_text(stats, noun, dry)
            self.run_page.set_cell(row, kind, text, color)
            self._cloud_stats.append(stats)
        for kind in self._cloud_kinds():
            if kind not in results:
                self.run_page.set_cell(row, kind, "Not available", t.MUTED)

    def _cloud_shared_done(self, index: int, stats: Stats) -> None:
        text, color = self._stats_text(stats, "files", self.run_kind == "dry")
        self.run_page.set_shared(index, text, color)
        self._cloud_stats.append(stats)

    def _cloud_finished(self, kind: str) -> None:
        worker = self.cloud_worker
        self.cloud_worker = None
        stopped = bool(worker and worker.stopped)
        self._finalize_run(kind, stopped)

    def _api_checks(self, accounts: list[Account], done_cb) -> None:
        """Read-only checks of contacts/calendars/files access for every mailbox."""
        kinds = self._cloud_kinds() + (["shared"] if "shared" in self.data_page.selected()
                                       else [])
        if not kinds:
            done_cb([])
            return
        src, dst = self._side(1), self._side(2)

        def work():
            problems = []
            for a in accounts:
                for n, side, user in ((1, src, a.user1), (2, dst, a.user2)):
                    if side.platform in ("google", "microsoft") and side.cred is None:
                        problems.append((n, a, "not signed in"))
                        continue
                    try:
                        check_access(side, user, kinds)
                    except (ApiError, auth.AuthError, OSError) as exc:
                        problems.append((n, a, str(exc)))
            return problems

        def done(problems, error):
            if error is not None:
                problems = [(1, accounts[0], str(error))]
            done_cb(problems or [])

        BgTask(work, done, self)
