"""Builds the right adapters for each side and runs the selected data types for a mailbox."""

from __future__ import annotations

import threading
import traceback
from dataclasses import dataclass, field

from .auth import Credential, api_resource
from .dav import CalDavCalendar, CardDavContacts, WebDavFiles
from .google import (GoogleCalendar, GoogleContacts, GoogleDirectory, GoogleDrive,
                     GoogleSharedDrives)
from .graph import GraphCalendar, GraphContacts, GraphDirectory, GraphDrive, GraphSites
from .http import ApiError, Client, Stopped
from .migrate import (CloudOptions, DrivePlan, Log, Progress, migrate_calendars,
                      migrate_contacts, migrate_files, migrate_shared_drive)
from .models import Drive, Stats
from .state import State

KINDS = ("contacts", "calendars", "files")
LABELS = {"mail": "Email", "contacts": "Contacts", "calendars": "Calendars", "files": "Files",
          "shared": "Shared drives"}


@dataclass
class Side:
    """One side of a migration, as far as the cloud engine is concerned."""
    platform: str                    # "google", "microsoft" or "dav"
    cred: Credential | None = None   # google / microsoft
    carddav: str = ""                # dav source addresses
    caldav: str = ""
    webdav: str = ""
    password: str = ""               # dav password (the IMAP one, usually)
    extra: dict = field(default_factory=dict)

    def supports(self, kind: str, as_source: bool) -> bool:
        if self.platform in ("google", "microsoft"):
            return kind != "shared" or bool(self.cred and self.cred.admin)
        if not as_source:
            return False
        return {"contacts": bool(self.carddav), "calendars": bool(self.caldav),
                "files": bool(self.webdav)}.get(kind, False)


def _client(cred: Credential, user: str, stop: threading.Event) -> tuple[Client, Credential]:
    acting = cred.for_user(user) if cred.admin else cred
    return Client(acting.source(api_resource(acting)), stop=stop), acting


def contacts_adapter(side: Side, user: str, stop: threading.Event):
    if side.platform == "google":
        return GoogleContacts(_client(side.cred, user, stop)[0])
    if side.platform == "microsoft":
        return GraphContacts(_client(side.cred, user, stop)[0], user, not side.cred.admin)
    return CardDavContacts(side.carddav, user, side.password, stop)


def calendar_adapter(side: Side, user: str, stop: threading.Event):
    if side.platform == "google":
        return GoogleCalendar(_client(side.cred, user, stop)[0])
    if side.platform == "microsoft":
        return GraphCalendar(_client(side.cred, user, stop)[0], user, not side.cred.admin)
    return CalDavCalendar(side.caldav, user, side.password, stop)


def files_adapter(side: Side, user: str, stop: threading.Event):
    if side.platform == "google":
        return GoogleDrive(_client(side.cred, user, stop)[0])
    if side.platform == "microsoft":
        return GraphDrive(_client(side.cred, user, stop)[0], user=user, me=not side.cred.admin)
    return WebDavFiles(side.webdav, user, side.password, stop)


def run_mailbox(src: Side, dst: Side, user1: str, user2: str, kinds: list[str],
                opts: CloudOptions, state_dir: str, stop: threading.Event,
                progress: Progress, log: Log) -> dict[str, Stats]:
    """Contacts, calendars and files for one mailbox pair (mail is imapsync's job)."""
    results: dict[str, Stats] = {}
    state = State(state_dir, f"{src.platform}:{user1}", f"{dst.platform}:{user2}")
    try:
        for kind in kinds:
            if kind not in KINDS:
                continue
            if not src.supports(kind, True) or not dst.supports(kind, False):
                log(f"{LABELS[kind]}: not available for this source or destination", "warning")
                continue
            log(f"{LABELS[kind]}: starting", "heading")
            try:
                if kind == "contacts":
                    stats = migrate_contacts(contacts_adapter(src, user1, stop),
                                             contacts_adapter(dst, user2, stop), state, opts,
                                             progress, log)
                elif kind == "calendars":
                    stats = migrate_calendars(calendar_adapter(src, user1, stop),
                                              calendar_adapter(dst, user2, stop), state, opts,
                                              dst.platform == "google", progress, log)
                else:
                    stats = migrate_files(files_adapter(src, user1, stop),
                                          files_adapter(dst, user2, stop), state, opts,
                                          progress=progress, log=log)
            except Stopped:
                raise
            except ApiError as exc:
                stats = Stats()
                stats.error(str(exc))
            except Exception as exc:  # keep going with the next data type
                stats = Stats()
                stats.error(f"Unexpected error: {exc}")
                log(traceback.format_exc(), "error")
            results[kind] = stats
            noun = {"contacts": "contacts", "calendars": "events", "files": "files"}[kind]
            verb_stats = stats.summary(noun)
            if opts.dry_run:
                verb_stats = verb_stats.replace("copied", "would be copied")
            log(f"{LABELS[kind]}: {verb_stats}", "ok" if not stats.failed else "error")
            for err in stats.errors[:20]:
                log(f"  {err}", "error" if stats.failed else "warning")
    finally:
        state.close()
    return results


# ================================================================ admin tools

def directory_users(side: Side) -> list[tuple[str, str]]:
    """All active users in the organization (admin mode)."""
    if not side.cred or not side.cred.admin:
        raise ApiError(0, "Listing users needs admin access.")
    client = Client(side.cred.source(api_resource(side.cred)))
    if side.platform == "google":
        return GoogleDirectory(client).users()
    return GraphDirectory(client).users()


def list_shared(side: Side) -> list[Drive]:
    """Shared drives (Google) or document libraries (Microsoft) visible to the admin."""
    client = Client(side.cred.source(api_resource(side.cred)))
    if side.platform == "google":
        return GoogleSharedDrives(client, admin=True).list()
    return GraphSites(client).all_libraries()


def _google_reader(side: Side, drive: Drive, stop) -> GoogleDrive:
    """Read a shared drive as one of its managers (the admin may not be a member)."""
    admin_client = Client(side.cred.source("google"), stop=stop)
    domain_users = [m for m in GoogleSharedDrives(admin_client, admin=True).members(drive.id)
                    if m.kind == "user" and m.email]
    managers = [m for m in domain_users if m.role in ("owner", "write")] or domain_users
    acting = side.cred
    if managers:
        acting = side.cred.for_user(managers[0].email)
    return GoogleDrive(Client(acting.source("google"), stop=stop), drive.id)


def run_shared_drives(src: Side, dst: Side, plans: list[DrivePlan], opts: CloudOptions,
                      state_dir: str, stop: threading.Event, progress: Progress,
                      log: Log) -> dict[str, Stats]:
    results: dict[str, Stats] = {}
    state = State(state_dir, f"{src.platform}:shared:{src.cred.email}",
                  f"{dst.platform}:shared:{dst.cred.email}")
    src_client = Client(src.cred.source(api_resource(src.cred)), stop=stop)
    dst_client = Client(dst.cred.source(api_resource(dst.cred)), stop=stop)
    try:
        for plan in plans:
            log(f"Shared drive “{plan.source.name}”: starting", "heading")
            if src.platform == "google":
                def open_source(d):
                    return _google_reader(src, d, stop)

                def members_of(d):
                    return GoogleSharedDrives(src_client, admin=True).members(d.id)
            else:
                def open_source(d):
                    return GraphDrive(src_client, d.id)

                def members_of(d):
                    return GraphSites(src_client).members(d.id)
            if dst.platform == "google":
                shared = GoogleSharedDrives(dst_client, admin=True)

                def open_dest(d):
                    return GoogleDrive(dst_client, d.id)

                def create_dest(name):
                    return shared.find(name) or shared.create(name)

                def add_member(d, perm):
                    shared.add_member(d.id, perm)
            else:
                sites = GraphSites(dst_client)

                def open_dest(d):
                    return GraphDrive(dst_client, d.id)

                def create_dest(name):
                    return sites.create_group_site(name)

                def add_member(d, perm):
                    sites.add_member(d.id, perm)
            try:
                stats = migrate_shared_drive(plan, open_source, open_dest, create_dest,
                                             members_of, add_member, state, opts, progress, log)
            except Stopped:
                raise
            except ApiError as exc:
                stats = Stats()
                stats.error(str(exc))
            results[plan.source.id] = stats
            log(f"Shared drive “{plan.source.name}”: {stats.summary('files')}",
                "ok" if not stats.failed else "error")
            for err in stats.errors[:20]:
                log(f"  {err}", "warning")
    finally:
        state.close()
    return results


def check_access(side: Side, user: str, kinds: list[str]) -> str:
    """A small read-only request proving the account works for the selected data.

    Returns a description; raises ApiError / AuthError with a readable message.
    """
    stop = threading.Event()
    if side.platform in ("google", "microsoft"):
        client, acting = _client(side.cred, user, stop)
        if side.platform == "google":
            data = client.json("GET", "https://www.googleapis.com/drive/v3/about",
                               params={"fields": "user(emailAddress)"})
            email = (data.get("user") or {}).get("emailAddress", user)
        else:
            from .graph import GRAPH, user_path
            data = client.json("GET", f"{GRAPH}{user_path(user, not side.cred.admin)}",
                               params={"$select": "mail,userPrincipalName"})
            email = data.get("mail") or data.get("userPrincipalName") or user
        return f"{email} — {'admin access' if side.cred.admin else 'signed in'}"
    checks = []
    if "contacts" in kinds and side.carddav:
        n = len(CardDavContacts(side.carddav, user, side.password, stop).dav.collections(
            "addressbook"))
        checks.append(f"{n} address book{'s' if n != 1 else ''}")
    if "calendars" in kinds and side.caldav:
        n = len(CalDavCalendar(side.caldav, user, side.password, stop).owned_calendars())
        checks.append(f"{n} calendar{'s' if n != 1 else ''}")
    if "files" in kinds and side.webdav:
        next(iter(WebDavFiles(side.webdav, user, side.password, stop).walk()), None)
        checks.append("files reachable")
    return ", ".join(checks) or "nothing to check"
