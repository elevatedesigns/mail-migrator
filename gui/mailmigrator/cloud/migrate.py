"""Copying contacts, calendars and files between any source and destination adapters."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Callable

from .http import ApiError, Stopped
from .models import Drive, Event, Stats
from .state import State, UserMap

Progress = Callable[[str, int, int, str], None]   # kind, done, total, message
Log = Callable[[str, str], None]                  # message, level ("", "warning", "error")


@dataclass
class CloudOptions:
    dry_run: bool = False
    since: datetime | None = None          # calendar: skip events that ended before this
    calendar_attendees: bool | None = None  # None: automatic (Google yes, Microsoft no)
    convert_documents: bool = False         # Office -> Google formats when copying to Google
    copy_permissions: bool = True
    max_file_mb: int = 0                    # 0 = no limit
    files_folder: str = ""                  # put copied files inside this folder
    user_map: UserMap = field(default_factory=UserMap)
    temp_dir: str | None = None


def _noop_progress(*_a) -> None:
    pass


def _noop_log(*_a) -> None:
    pass


# ================================================================== contacts

def migrate_contacts(src, dst, state: State, opts: CloudOptions,
                     progress: Progress = _noop_progress, log: Log = _noop_log) -> Stats:
    stats = Stats()
    progress("contacts", 0, 0, "Reading contacts…")
    contacts = list(src.list_contacts())
    total = len(contacts)
    pending = []
    for i, c in enumerate(contacts, 1):
        key = c.source_id or c.fingerprint()
        fp = c.fingerprint()
        known = state.get("contact", key)
        if known and known[1] == fp:
            stats.skipped += 1
        elif known and known[0] and not opts.dry_run:
            try:
                dst.update_contact(known[0], c)
                state.put("contact", key, known[0], fp)
                stats.updated += 1
            except ApiError as exc:
                if exc.status == 404:  # deleted at the destination: create it again
                    pending.append((key, fp, c))
                else:
                    stats.error(f"Contact {c.label()}: {exc}")
        elif known:
            stats.updated += 1
        else:
            pending.append((key, fp, c))
        progress("contacts", i, total, f"Checked {i:,} of {total:,} contacts")

    if opts.dry_run:
        stats.created += len(pending)
        return stats
    done = total - len(pending)
    for start in range(0, len(pending), 100):
        chunk = pending[start:start + 100]
        try:
            ids = dst.create_contacts([c for _, _, c in chunk])
        except ApiError as exc:
            ids = []
            # Retry one by one so a single bad contact doesn't sink the batch.
            for key, fp, c in chunk:
                try:
                    ids.append(dst.create_contacts([c])[0])
                except ApiError as one:
                    ids.append("")
                    stats.error(f"Contact {c.label()}: {one}")
            if not any(ids):
                log(f"Contacts could not be created: {exc}", "error")
        for (key, fp, c), new_id in zip(chunk, ids):
            if new_id:
                state.put("contact", key, new_id, fp)
                stats.created += 1
        done += len(chunk)
        progress("contacts", done, total, f"Copied {stats.created:,} contacts")
    return stats


# ================================================================== calendars

def _map_event(ev: Event, umap: UserMap) -> Event:
    if not umap:
        return ev
    attendees = [replace(a, email=umap.map(a.email)) for a in ev.attendees]
    return replace(ev, attendees=attendees, organizer=umap.map(ev.organizer))


def migrate_calendars(src, dst, state: State, opts: CloudOptions, dest_is_google: bool,
                      progress: Progress = _noop_progress, log: Log = _noop_log) -> Stats:
    stats = Stats()
    include_attendees = (opts.calendar_attendees if opts.calendar_attendees is not None
                         else dest_is_google)
    progress("calendars", 0, 0, "Reading calendars…")
    calendars = src.owned_calendars()
    for cal in calendars:
        try:
            events = sorted(src.list_events(cal.id, opts.since), key=lambda e: e.is_exception)
        except ApiError as exc:
            stats.error(f"Calendar {cal.name}: {exc}")
            continue
        dest_cal = ""
        if not opts.dry_run:
            try:
                dest_cal = dst.ensure_calendar(cal.name, cal.primary)
            except ApiError as exc:
                stats.error(f"Calendar {cal.name}: {exc}")
                continue
        recreated: set[str] = set()
        series: dict[str, str] = {}
        total = len(events)
        for i, ev in enumerate(events, 1):
            ev = _map_event(ev, opts.user_map)
            if ev.is_exception:
                key = f"{cal.id}|{ev.uid}|{ev.original_start}"
            else:
                key = f"{cal.id}|{ev.source_id}"
            fp = ev.fingerprint()
            known = state.get("event", key)
            if known and known[1] == fp and not (ev.is_exception and ev.uid in recreated):
                stats.skipped += 1
                if not ev.is_exception and ev.recurrence:
                    series[ev.uid] = known[0]
                progress("calendars", i, total, f"{cal.name}: {i:,} of {total:,} events")
                continue
            if opts.dry_run:
                stats.updated += 1 if known else 0
                stats.created += 0 if known else 1
                continue
            try:
                if known and known[0] and not ev.is_exception:
                    dst.delete_event(dest_cal, known[0])   # changed: replace it
                    if ev.recurrence:
                        recreated.add(ev.uid)
                master = series.get(ev.uid) if ev.is_exception else None
                if ev.is_exception and not master:
                    rec = state.get("series", f"{cal.id}|{ev.uid}")
                    master = rec[0] if rec else None
                new_id = dst.import_event(dest_cal, ev, include_attendees, master)
                state.put("event", key, new_id, fp)
                if ev.recurrence and not ev.is_exception:
                    series[ev.uid] = new_id
                    state.put("series", f"{cal.id}|{ev.uid}", new_id)
                if known:
                    stats.updated += 1
                else:
                    stats.created += 1
            except ApiError as exc:
                stats.error(f"{cal.name}: “{ev.summary or '(no title)'}” "
                            f"{ev.start}: {exc}")
            progress("calendars", i, total, f"{cal.name}: {i:,} of {total:,} events")
    return stats


# ================================================================== files

def _unique(name: str, used: set[str]) -> str:
    if name.lower() not in used:
        used.add(name.lower())
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    n = 2
    while True:
        candidate = f"{stem} ({n}){'.' + ext if ext else ''}"
        if candidate.lower() not in used:
            used.add(candidate.lower())
            return candidate
        n += 1


def _share(src, dst, item_id: str, dest_id: str, opts: CloudOptions, stats: Stats,
           label: str) -> None:
    try:
        perms = src.permissions(item_id)
    except ApiError as exc:
        stats.errors.append(f"Sharing of {label} could not be read: {exc}")
        return
    for p in perms:
        domain = opts.user_map.map_domain(p.domain) if p.domain else ""
        if p.kind == "domain" and not domain and opts.user_map.domains:
            # "Anyone in the organization" from Microsoft: use the new domain.
            domain = next(iter(opts.user_map.domains.values()))
        mapped = replace(p, email=opts.user_map.map(p.email), domain=domain)
        try:
            dst.share(dest_id, mapped)
        except ApiError as exc:
            who = mapped.email or mapped.domain or mapped.kind
            stats.errors.append(f"Sharing {label} with {who}: {exc}")


def migrate_files(src, dst, state: State, opts: CloudOptions, kind: str = "files",
                  src_root: str | None = None, dest_root: tuple[str, ...] = (),
                  progress: Progress = _noop_progress, log: Log = _noop_log) -> Stats:
    stats = Stats()
    root = dest_root + ((opts.files_folder,) if opts.files_folder else ())
    used_names: dict[tuple[str, ...], set[str]] = {}
    existing_cache: dict[str, dict[str, tuple[str, int]]] = {}
    limit = opts.max_file_mb * 1_000_000 if opts.max_file_mb else 0
    count = 0
    progress(kind, 0, 0, "Listing files…")
    for item in src.walk(src_root):
        count += 1
        label = "/".join(item.path + (item.name,))
        if item.skip_reason:
            stats.skipped += 1
            log(f"Skipped {label}: {item.skip_reason}", "warning")
            continue
        folder_path = item.path
        used = used_names.setdefault(folder_path, set())
        if item.is_folder:
            name = _unique(item.name, used)
            if opts.dry_run:
                continue
            try:
                folder_id = dst.ensure_folder(folder_path + (name,), root)
                if opts.copy_permissions and not state.get(kind + "-folder-shared", item.id):
                    _share(src, dst, item.id, folder_id, opts, stats, label)
                    state.put(kind + "-folder-shared", item.id, folder_id)
            except ApiError as exc:
                stats.error(f"Folder {label}: {exc}")
            continue

        name = _unique(item.target_name, used)
        fp = f"{item.size}|{item.modified.isoformat() if item.modified else ''}"
        known = state.get(kind, item.id)
        if known and known[1] == fp:
            stats.skipped += 1
            progress(kind, count, 0, f"{count:,} items checked")
            continue
        if limit and item.size > limit:
            stats.skipped += 1
            log(f"Skipped {label}: larger than {opts.max_file_mb} MB", "warning")
            continue
        if opts.dry_run:
            stats.created += 1
            stats.bytes += item.size
            continue
        try:
            folder_id = dst.ensure_folder(folder_path, root)
            if not known and not item.native:
                if folder_id not in existing_cache:
                    existing_cache[folder_id] = dst.existing(folder_id)
                hit = existing_cache[folder_id].get(name)
                if hit and hit[1] == item.size:   # copied earlier by another tool or run
                    state.put(kind, item.id, hit[0], fp)
                    stats.skipped += 1
                    continue
            with tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024,
                                               dir=opts.temp_dir) as tmp:
                size = src.download(item, tmp)
                new_id = dst.upload(folder_id, name, tmp, size, item.modified,
                                    convert=opts.convert_documents)
            state.put(kind, item.id, new_id, fp)
            stats.bytes += size
            if known:
                stats.updated += 1
            else:
                stats.created += 1
            if opts.copy_permissions:
                _share(src, dst, item.id, new_id, opts, stats, label)
        except Stopped:
            raise
        except ApiError as exc:
            stats.error(f"{label}: {exc}")
        except OSError as exc:
            stats.error(f"{label}: {exc}")
        progress(kind, count, 0, f"{count:,} items checked · {stats.created:,} copied")
    return stats


# ============================================================== shared drives

@dataclass
class DrivePlan:
    source: Drive
    dest: Drive | None = None      # None: create a new shared drive / group site
    dest_name: str = ""


def migrate_shared_drive(plan: DrivePlan, open_source, open_dest, create_dest, members_of,
                         add_member, state: State, opts: CloudOptions,
                         progress: Progress = _noop_progress, log: Log = _noop_log) -> Stats:
    """Copy one shared drive / document library, its members and its contents.

    The callables keep this independent of the platforms involved:
      open_source(drive) -> file adapter, open_dest(drive) -> file adapter,
      create_dest(name) -> Drive, members_of(drive) -> [Permission],
      add_member(dest_drive, Permission).
    """
    stats = Stats()
    name = plan.dest_name or plan.source.name
    dest = plan.dest
    if dest is None:
        known = state.get("shared-drive", plan.source.id)
        if known:
            dest = Drive(known[0], name)
        elif opts.dry_run:
            log(f"Would create “{name}”", "")
        else:
            progress("shared", 0, 0, f"Creating “{name}”…")
            dest = create_dest(name)
            state.put("shared-drive", plan.source.id, dest.id)
            log(f"Created “{name}”", "")
    if opts.copy_permissions and dest is not None and not opts.dry_run:
        for member in members_of(plan.source):
            mapped = replace(member, email=opts.user_map.map(member.email))
            try:
                add_member(dest, mapped)
            except ApiError as exc:
                stats.errors.append(f"Adding {mapped.email or mapped.domain} to {name}: {exc}")
    if dest is None:   # dry run of a drive that would be created
        src = open_source(plan.source)
        for item in src.walk():
            if not item.is_folder and not item.skip_reason:
                stats.created += 1
                stats.bytes += item.size
        return stats
    file_stats = migrate_files(open_source(plan.source), open_dest(dest), state, opts,
                               kind=f"shared:{plan.source.id}", progress=progress, log=log)
    for attr in ("created", "updated", "skipped", "failed", "bytes"):
        setattr(stats, attr, getattr(stats, attr) + getattr(file_stats, attr))
    stats.errors += file_stats.errors
    return stats

