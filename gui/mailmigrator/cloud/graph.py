"""Microsoft 365 / Outlook.com through Microsoft Graph: contacts, calendars,
OneDrive and SharePoint files, sharing and the user directory."""

from __future__ import annotations

import hashlib
import io
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import BinaryIO, Iterator
from urllib.parse import quote

from .http import ApiError, Client
from .models import Address, Attendee, Calendar, Contact, Drive, Event, FileItem, Permission
from .recurrence import (IANA_TO_WINDOWS, Unsupported, expand, graph_to_rrule, local_parts,
                         parse_local, rrule_to_graph, to_iana)

GRAPH = "https://graph.microsoft.com/v1.0"
UTC_PREFER = {"Prefer": 'outlook.timezone="UTC", outlook.body-content-type="html"'}
SIMPLE_UPLOAD_LIMIT = 4 * 1024 * 1024
CHUNK = 320 * 1024 * 32       # 10 MiB, a multiple of 320 KiB as Graph requires
NO_YEAR = 1604                # Outlook's year for birthdays without a year
INVALID_NAME = re.compile(r'["*:<>?/\\|]')
RESERVED = {".lock", "con", "prn", "aux", "nul", "desktop.ini", "_vti_"} | \
    {f"com{i}" for i in range(10)} | {f"lpt{i}" for i in range(10)}


def user_path(email: str, me: bool) -> str:
    return "/me" if me else f"/users/{quote(email)}"


def safe_name(name: str) -> str:
    """OneDrive/SharePoint refuse some characters and names."""
    clean = INVALID_NAME.sub("_", name).strip().rstrip(".")
    if not clean or clean.lower() in RESERVED or clean.startswith("~$"):
        clean = "_" + clean
    return clean[:250]


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# =================================================================== contacts

def graph_to_contact(g: dict, group: str = "") -> Contact:
    c = Contact(source_id=g["id"], display_name=g.get("displayName") or "",
                given_name=g.get("givenName") or "", family_name=g.get("surname") or "",
                middle_name=g.get("middleName") or "", prefix=g.get("title") or "",
                suffix=g.get("generation") or "", nickname=g.get("nickName") or "",
                company=g.get("companyName") or "", job_title=g.get("jobTitle") or "",
                department=g.get("department") or "", notes=g.get("personalNotes") or "")
    c.emails = [("other", e["address"]) for e in g.get("emailAddresses") or []
                if e.get("address")]
    if g.get("mobilePhone"):
        c.phones.append(("mobile", g["mobilePhone"]))
    c.phones += [("work", p) for p in g.get("businessPhones") or [] if p]
    c.phones += [("home", p) for p in g.get("homePhones") or [] if p]
    for key, kind in (("homeAddress", "home"), ("businessAddress", "work"),
                      ("otherAddress", "other")):
        a = g.get(key) or {}
        addr = Address(kind, a.get("street") or "", a.get("city") or "", a.get("state") or "",
                       a.get("postalCode") or "", a.get("countryOrRegion") or "")
        if not addr.empty():
            c.addresses.append(addr)
    if g.get("birthday"):
        d = g["birthday"][:10]
        c.birthday = ("--" + d[5:]) if d.startswith(str(NO_YEAR)) else d
    if g.get("businessHomePage"):
        c.websites.append(g["businessHomePage"])
    c.groups = [x for x in (g.get("categories") or []) if x]
    if group and group not in c.groups:
        c.groups.append(group)
    return c


def contact_to_graph(c: Contact) -> dict:
    g: dict = {k: v for k, v in (
        ("givenName", c.given_name), ("surname", c.family_name), ("middleName", c.middle_name),
        ("title", c.prefix), ("generation", c.suffix), ("nickName", c.nickname),
        ("displayName", c.display_name), ("companyName", c.company),
        ("jobTitle", c.job_title), ("department", c.department)) if v}
    if not g.get("displayName"):
        g["displayName"] = c.label()
    extra: list[str] = []
    g["emailAddresses"] = [{"address": v, "name": c.label()} for _, v in c.emails[:3]]
    extra += [f"Email: {v}" for _, v in c.emails[3:]]
    mobile = [v for k, v in c.phones if k == "mobile"]
    work = [v for k, v in c.phones if k == "work"]
    home = [v for k, v in c.phones if k == "home"]
    other = [v for k, v in c.phones if k == "other"]
    if mobile:
        g["mobilePhone"] = mobile[0]
        extra += [f"Mobile: {v}" for v in mobile[1:]]
    g["businessPhones"] = work[:2]
    g["homePhones"] = home[:2]
    extra += [f"Phone: {v}" for v in work[2:] + home[2:] + other]
    used = set()
    for a in c.addresses:
        key = {"home": "homeAddress", "work": "businessAddress"}.get(a.kind, "otherAddress")
        if key in used:
            key = "otherAddress" if "otherAddress" not in used else None
        if key is None:
            extra.append("Address: " + ", ".join(x for x in (a.street, a.city, a.region,
                                                             a.postal_code, a.country) if x))
            continue
        used.add(key)
        g[key] = {"street": a.street, "city": a.city, "state": a.region,
                  "postalCode": a.postal_code, "countryOrRegion": a.country}
    if c.birthday:
        b = c.birthday
        b = f"{NO_YEAR}-{b[2:]}" if b.startswith("--") else b
        g["birthday"] = f"{b}T11:59:00Z"
    if c.websites:
        g["businessHomePage"] = c.websites[0]
        extra += [f"Website: {u}" for u in c.websites[1:]]
    notes = c.notes
    if extra:
        notes = (notes + "\n\n" if notes else "") + "\n".join(extra)
    if notes:
        g["personalNotes"] = notes
    if c.groups:
        g["categories"] = c.groups
    return g


class GraphContacts:
    def __init__(self, client: Client, user: str, me: bool) -> None:
        self.c = client
        self.u = user_path(user, me)

    def list_contacts(self) -> Iterator[Contact]:
        for g in self.c.pages(f"{GRAPH}{self.u}/contacts", "value", {"$top": 500},
                              next_key="@odata.nextLink"):
            yield graph_to_contact(g)
        for folder in self._folders(f"{GRAPH}{self.u}/contactFolders"):
            for g in self.c.pages(f"{GRAPH}{self.u}/contactFolders/{folder['id']}/contacts",
                                  "value", {"$top": 500}, next_key="@odata.nextLink"):
                yield graph_to_contact(g, folder.get("displayName", ""))

    def _folders(self, url: str) -> Iterator[dict]:
        for f in self.c.pages(url, "value", {"$top": 100}, next_key="@odata.nextLink"):
            yield f
            yield from self._folders(f"{GRAPH}{self.u}/contactFolders/{f['id']}/childFolders")

    def create_contacts(self, contacts: list[Contact]) -> list[str]:
        return [self.c.json("POST", f"{GRAPH}{self.u}/contacts",
                            json=contact_to_graph(c))["id"] for c in contacts]

    def update_contact(self, contact_id: str, contact: Contact) -> None:
        self.c.json("PATCH", f"{GRAPH}{self.u}/contacts/{contact_id}",
                    json=contact_to_graph(contact))


# =================================================================== calendar

RESPONSE_FROM = {"accepted": "accepted", "declined": "declined",
                 "tentativelyAccepted": "tentative", "none": "needsAction",
                 "notResponded": "needsAction", "organizer": "accepted"}


def _g_when(t: dict | None, all_day: bool):
    if not t:
        return None
    if all_day:
        return date.fromisoformat(t["dateTime"][:10])
    return parse_local(t["dateTime"], t.get("timeZone") or "UTC")


def graph_to_event(g: dict) -> Event:
    all_day = bool(g.get("isAllDay"))
    tz = to_iana(g.get("originalStartTimeZone") or "UTC")
    body = g.get("body") or {}
    ev = Event(source_id=g["id"], uid=g.get("iCalUId", ""), summary=g.get("subject") or "",
               description=body.get("content") or "",
               html=(body.get("contentType") or "").lower() == "html",
               location=(g.get("location") or {}).get("displayName") or "",
               start=_g_when(g.get("start"), all_day), end=_g_when(g.get("end"), all_day),
               all_day=all_day, timezone=tz, cancelled=bool(g.get("isCancelled")),
               organizer=((g.get("organizer") or {}).get("emailAddress") or {}).get("address", ""),
               private=(g.get("sensitivity") or "") in ("private", "confidential"),
               free=(g.get("showAs") or "") == "free")
    if g.get("recurrence"):
        try:
            ev.recurrence = [graph_to_rrule(g["recurrence"])]
        except Unsupported:
            ev.recurrence = []
    for a in g.get("attendees") or []:
        addr = (a.get("emailAddress") or {}).get("address")
        if not addr or a.get("type") == "resource":
            continue
        ev.attendees.append(Attendee(addr, (a.get("emailAddress") or {}).get("name", ""),
                                     RESPONSE_FROM.get((a.get("status") or {}).get("response",
                                                                                  "none"),
                                                       "needsAction"),
                                     a.get("type") == "optional"))
    if g.get("isReminderOn") and g.get("reminderMinutesBeforeStart") is not None:
        ev.reminders = [int(g["reminderMinutesBeforeStart"])]
    return ev


def _attendee_note(ev: Event) -> str:
    if not ev.attendees:
        return ""
    names = ", ".join(f"{a.name} <{a.email}>" if a.name else a.email for a in ev.attendees)
    org = f"Organizer: {ev.organizer}\n" if ev.organizer else ""
    return f"\n\n— Copied by Mail Migrator —\n{org}Attendees: {names}"


def event_to_graph(ev: Event, include_attendees: bool) -> dict:
    tz = ev.timezone or "UTC"
    win_tz = IANA_TO_WINDOWS.get(tz, tz)

    def when(value, end: bool = False):
        if isinstance(value, datetime):
            return {"dateTime": local_parts(value, tz), "timeZone": win_tz}
        d = value
        return {"dateTime": f"{d.isoformat()}T00:00:00", "timeZone": win_tz}

    end = ev.end or ev.start
    if ev.all_day and isinstance(end, date) and not isinstance(end, datetime) and end <= ev.start:
        end = ev.start + timedelta(days=1)
    desc = ev.description
    note = "" if include_attendees else _attendee_note(ev)
    if note:
        desc = (desc + (note.replace("\n", "<br>") if ev.html else note)) if desc else note.strip()
    body: dict = {"subject": ev.summary,
                  "body": {"contentType": "html" if ev.html else "text", "content": desc},
                  "start": when(ev.start), "end": when(end, True), "isAllDay": ev.all_day,
                  "location": {"displayName": ev.location},
                  "sensitivity": "private" if ev.private else "normal",
                  "showAs": "free" if ev.free else "busy",
                  "isReminderOn": bool(ev.reminders),
                  "transactionId": str(uuid.UUID(hashlib.md5(
                      (ev.uid + ev.source_id).encode()).hexdigest()))}
    if ev.reminders:
        body["reminderMinutesBeforeStart"] = ev.reminders[0]
    if include_attendees and ev.attendees:
        # Note: Exchange sends invitations for events created with attendees.
        body["attendees"] = [{"emailAddress": {"address": a.email, "name": a.name or a.email},
                              "type": "optional" if a.optional else "required"}
                             for a in ev.attendees]
    return body


class GraphCalendar:
    def __init__(self, client: Client, user: str, me: bool, window_years: int = 3) -> None:
        self.c = client
        self.user = user
        self.u = user_path(user, me)
        self.window_years = window_years

    def list_calendars(self) -> list[Calendar]:
        out = []
        for c in self.c.pages(f"{GRAPH}{self.u}/calendars", "value", {"$top": 100},
                              next_key="@odata.nextLink"):
            owner = ((c.get("owner") or {}).get("address") or "").lower()
            mine = not owner or not self.user or owner == self.user.lower() or \
                c.get("isDefaultCalendar")
            out.append(Calendar(c["id"], c.get("name", ""), bool(c.get("isDefaultCalendar")),
                                not (c.get("canEdit") and mine), c.get("hexColor", "")))
        return out

    def owned_calendars(self) -> list[Calendar]:
        return [c for c in self.list_calendars() if not c.read_only]

    def list_events(self, cal_id: str, since: datetime | None = None) -> Iterator[Event]:
        for g in self.c.pages(f"{GRAPH}{self.u}/calendars/{cal_id}/events", "value",
                              {"$top": 250}, next_key="@odata.nextLink", headers=UTC_PREFER):
            ev = graph_to_event(g)
            if g.get("type") == "seriesMaster":
                yield from self._series(g, ev, since)
            elif since is None or _ends_after(ev, since):
                yield ev

    def _series(self, g: dict, master: Event, since: datetime | None) -> Iterator[Event]:
        """The series, its changed occurrences, and EXDATEs for deleted ones."""
        if not master.recurrence or master.start is None:
            yield master
            return
        start = master.start
        begin = since or (start if isinstance(start, datetime) else
                          datetime(start.year, start.month, start.day, tzinfo=timezone.utc))
        if isinstance(begin, date) and not isinstance(begin, datetime):
            begin = datetime(begin.year, begin.month, begin.day, tzinfo=timezone.utc)
        anchor = max(datetime.now(timezone.utc), begin)
        finish = anchor + timedelta(days=365 * self.window_years)
        seen: set[str] = set()
        exceptions = []
        for inst in self.c.pages(f"{GRAPH}{self.u}/events/{g['id']}/instances", "value",
                                 {"startDateTime": begin.isoformat(),
                                  "endDateTime": finish.isoformat(), "$top": 500},
                                 next_key="@odata.nextLink", headers=UTC_PREFER):
            orig = inst.get("originalStart")
            if orig:
                seen.add(orig[:16])
            if inst.get("type") == "exception":
                ex = graph_to_event(inst)
                ex.uid = master.uid
                ex.recurrence = []
                ex.original_start = (_parse_dt(orig) if orig and not master.all_day
                                     else (date.fromisoformat(orig[:10]) if orig else None))
                exceptions.append(ex)
        # Occurrences missing from the instance list were deleted: record them as EXDATEs.
        if not master.all_day:
            local_start = start.astimezone(to_zone(master.timezone))
            try:
                expected = expand(master.recurrence[0], local_start,
                                  finish.astimezone(to_zone(master.timezone)))
            except (ValueError, TypeError):
                expected = []
            for occ in expected:
                occ_utc = occ.astimezone(timezone.utc)
                if occ_utc < begin:
                    continue
                key = occ_utc.strftime("%Y-%m-%dT%H:%M")
                if key not in seen:
                    master.recurrence.append("EXDATE:" + occ_utc.strftime("%Y%m%dT%H%M%SZ"))
        yield master
        yield from exceptions

    def ensure_calendar(self, name: str, primary: bool) -> str:
        cals = self.owned_calendars()
        if primary:
            for c in cals:
                if c.primary:
                    return c.id
        for c in cals:
            if c.name == name:
                return c.id
        return self.c.json("POST", f"{GRAPH}{self.u}/calendars", json={"name": name})["id"]

    def import_event(self, cal_id: str, ev: Event, include_attendees: bool = False,
                     master_dest_id: str | None = None) -> str:
        if ev.is_exception:
            if not master_dest_id:
                raise ApiError(0, "The recurring series for this changed occurrence was not "
                                  "copied")
            return self._apply_exception(master_dest_id, ev, include_attendees)
        body = event_to_graph(ev, include_attendees)
        exdates = [r for r in ev.recurrence if r.upper().startswith("EXDATE")]
        rrules = [r for r in ev.recurrence if r.upper().startswith("RRULE")]
        if rrules:
            start = ev.start if not isinstance(ev.start, datetime) else \
                ev.start.astimezone(to_zone(ev.timezone)).date()
            try:
                body["recurrence"] = rrule_to_graph(rrules[0], start,
                                                    IANA_TO_WINDOWS.get(ev.timezone, ev.timezone))
            except Unsupported:
                return self._expand_into_singles(cal_id, ev, include_attendees)
        created = self.c.json("POST", f"{GRAPH}{self.u}/calendars/{cal_id}/events", json=body)
        for line in exdates:
            for value in line.split(":", 1)[1].split(","):
                self._delete_occurrence(created["id"], _exdate(value, ev.timezone))
        return created["id"]

    def _expand_into_singles(self, cal_id: str, ev: Event, include_attendees: bool) -> str:
        """Graph cannot express this rule: create each occurrence as its own event."""
        if not isinstance(ev.start, datetime):
            starts = expand(ev.recurrence[0], ev.start,
                            date.today() + timedelta(days=365 * self.window_years))
        else:
            local = ev.start.astimezone(to_zone(ev.timezone))
            starts = expand(ev.recurrence[0], local,
                            datetime.now(timezone.utc).astimezone(to_zone(ev.timezone)) +
                            timedelta(days=365 * self.window_years))
        duration = (ev.end - ev.start) if ev.end else timedelta(hours=1)
        first = ""
        for s in starts:
            single = Event(**{**ev.__dict__, "recurrence": [], "start": s, "end": s + duration,
                              "source_id": f"{ev.source_id}@{s}"})
            new_id = self.c.json("POST", f"{GRAPH}{self.u}/calendars/{cal_id}/events",
                                 json=event_to_graph(single, include_attendees))["id"]
            first = first or new_id
        return first

    def _find_instance(self, master_id: str, original) -> dict | None:
        if isinstance(original, datetime):
            lo, hi = original - timedelta(days=1), original + timedelta(days=1)
            key = original.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")
        else:
            lo = datetime(original.year, original.month, original.day,
                          tzinfo=timezone.utc) - timedelta(days=1)
            hi, key = lo + timedelta(days=3), original.isoformat()
        for inst in self.c.pages(f"{GRAPH}{self.u}/events/{master_id}/instances", "value",
                                 {"startDateTime": lo.isoformat(), "endDateTime": hi.isoformat()},
                                 next_key="@odata.nextLink", headers=UTC_PREFER):
            if (inst.get("originalStart") or "").startswith(key):
                return inst
        return None

    def _delete_occurrence(self, master_id: str, original) -> None:
        inst = self._find_instance(master_id, original)
        if inst:
            self.c.request("DELETE", f"{GRAPH}{self.u}/events/{inst['id']}")

    def _apply_exception(self, master_id: str, ev: Event, include_attendees: bool) -> str:
        inst = self._find_instance(master_id, ev.original_start)
        if not inst:
            return ""
        if ev.cancelled:
            self.c.request("DELETE", f"{GRAPH}{self.u}/events/{inst['id']}")
            return inst["id"]
        body = event_to_graph(ev, include_attendees)
        body.pop("transactionId", None)
        self.c.json("PATCH", f"{GRAPH}{self.u}/events/{inst['id']}", json=body)
        return inst["id"]

    def delete_event(self, cal_id: str, event_id: str) -> None:
        try:
            self.c.request("DELETE", f"{GRAPH}{self.u}/events/{event_id}")
        except ApiError as exc:
            if exc.status != 404:
                raise


def _ends_after(ev: Event, since: datetime) -> bool:
    end = ev.end or ev.start
    if end is None:
        return True
    if not isinstance(end, datetime):
        return end >= since.date()
    return end >= since


def to_zone(name: str):
    from .recurrence import zone
    return zone(name)


def _exdate(value: str, tz: str):
    value = value.strip()
    if "T" not in value:
        return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    dt = datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        return dt.replace(tzinfo=timezone.utc)
    return dt.replace(tzinfo=to_zone(tz)).astimezone(timezone.utc)


# =================================================================== files

def graph_file(g: dict, path: tuple[str, ...], drive_id: str) -> FileItem:
    item = FileItem(id=g["id"], name=g.get("name", ""), is_folder="folder" in g,
                    size=int(g.get("size") or 0),
                    mime=(g.get("file") or {}).get("mimeType", ""),
                    modified=_parse_dt((g.get("fileSystemInfo") or {}).get("lastModifiedDateTime")
                                       or g.get("lastModifiedDateTime")),
                    drive_id=(g.get("parentReference") or {}).get("driveId", drive_id),
                    path=path)
    if "package" in g:
        item.skip_reason = "OneNote notebooks cannot be copied as files"
        item.is_folder = False
    elif "remoteItem" in g:
        item.skip_reason = "Shared with you by someone else (stays with its owner)"
    return item


GRAPH_ROLE = {"read": "read", "write": "write", "owner": "owner", "sp.full control": "owner",
              "sp.edit": "write", "sp.view": "read"}


class GraphDrive:
    """A OneDrive or a SharePoint document library, as source and destination."""

    def __init__(self, client: Client, drive_id: str = "", user: str = "", me: bool = True):
        self.c = client
        self.user, self.me = user, me
        self._drive_id = drive_id
        self._folders: dict[tuple[str, ...], str] = {}

    @property
    def drive_id(self) -> str:
        if not self._drive_id:
            self._drive_id = self.c.json("GET", f"{GRAPH}{user_path(self.user, self.me)}/drive",
                                         params={"$select": "id"})["id"]
        return self._drive_id

    def root_id(self) -> str:
        return self.c.json("GET", f"{GRAPH}/drives/{self.drive_id}/root",
                           params={"$select": "id"})["id"]

    def _children(self, item_id: str) -> Iterator[dict]:
        yield from self.c.pages(f"{GRAPH}/drives/{self.drive_id}/items/{item_id}/children",
                                "value", {"$top": 999}, next_key="@odata.nextLink")

    def walk(self, folder_id: str | None = None, path: tuple[str, ...] = ()) -> Iterator[FileItem]:
        folder_id = folder_id or self.root_id()
        items = sorted(self._children(folder_id),
                       key=lambda g: ("folder" not in g, g.get("name", "").lower()))
        for g in items:
            item = graph_file(g, path, self.drive_id)
            yield item
            if item.is_folder:
                yield from self.walk(item.id, path + (item.name,))

    def download(self, item: FileItem, out: BinaryIO) -> int:
        resp = self.c.request("GET", f"{GRAPH}/drives/{self.drive_id}/items/{item.id}/content",
                              stream=True)
        total = 0
        for chunk in resp.iter_content(1024 * 1024):
            self.c.check_stop()
            out.write(chunk)
            total += len(chunk)
        return total

    def permissions(self, item_id: str) -> list[Permission]:
        out = []
        for p in self.c.pages(f"{GRAPH}/drives/{self.drive_id}/items/{item_id}/permissions",
                              "value", next_key="@odata.nextLink"):
            if p.get("inheritedFrom"):
                continue
            roles = [r.lower() for r in p.get("roles") or []]
            if "owner" in roles:
                continue
            role = "write" if "write" in roles else "read"
            link = p.get("link") or {}
            if link:
                scope = link.get("scope")
                role = "write" if link.get("type") == "edit" else "read"
                if scope == "anonymous":
                    out.append(Permission("", role, "anyone"))
                elif scope == "organization":
                    out.append(Permission("", role, "domain"))
                continue
            granted = [p.get("grantedToV2") or {}] + list(p.get("grantedToIdentitiesV2") or [])
            for g in granted:
                ident = g.get("user") or g.get("group") or {}
                email = ident.get("email") or ident.get("loginName") or ""
                if "@" in email:
                    out.append(Permission(email, role, "group" if "group" in g else "user"))
        return out

    # -- destination --

    def ensure_folder(self, path: tuple[str, ...], root: tuple[str, ...] = ()) -> str:
        full = root + path
        if not full:
            return self.root_id()
        if full in self._folders:
            return self._folders[full]
        parent = self.ensure_folder(full[:-1])
        name = safe_name(full[-1])
        try:
            folder = self.c.json("POST", f"{GRAPH}/drives/{self.drive_id}/items/{parent}/children",
                                 json={"name": name, "folder": {},
                                       "@microsoft.graph.conflictBehavior": "fail"})
        except ApiError as exc:
            if exc.status != 409:
                raise
            folder = self.c.json("GET", f"{GRAPH}/drives/{self.drive_id}/items/{parent}:/"
                                        f"{quote(name)}", params={"$select": "id"})
        self._folders[full] = folder["id"]
        return folder["id"]

    def existing(self, folder_id: str) -> dict[str, tuple[str, int]]:
        return {g.get("name", ""): (g["id"], int(g.get("size") or 0))
                for g in self._children(folder_id) if "folder" not in g}

    def upload(self, folder_id: str, name: str, data: BinaryIO, size: int,
               modified: datetime | None = None, convert: bool = False) -> str:
        name = safe_name(name)
        target = f"{GRAPH}/drives/{self.drive_id}/items/{folder_id}:/{quote(name)}:"
        fs_info = ({"lastModifiedDateTime": modified.astimezone(timezone.utc).isoformat()
                    .replace("+00:00", "Z")} if modified else None)
        if size <= SIMPLE_UPLOAD_LIMIT:
            data.seek(0)
            item = self.c.json("PUT", f"{target}/content",
                               params={"@microsoft.graph.conflictBehavior": "replace"},
                               data=data.read(),
                               headers={"Content-Type": "application/octet-stream"})
            if fs_info:
                self.c.json("PATCH", f"{GRAPH}/drives/{self.drive_id}/items/{item['id']}",
                            json={"fileSystemInfo": fs_info})
            return item["id"]
        item_props: dict = {"@microsoft.graph.conflictBehavior": "replace"}
        if fs_info:
            item_props["fileSystemInfo"] = fs_info
        session = self.c.json("POST", f"{target}/createUploadSession", json={"item": item_props})
        url = session["uploadUrl"]
        offset = 0
        data.seek(0)
        while offset < size:
            self.c.check_stop()
            chunk = data.read(CHUNK)
            if not chunk:
                break
            end = offset + len(chunk) - 1
            resp = self.c.request("PUT", url, auth=False, data=io.BytesIO(chunk),
                                  headers={"Content-Range": f"bytes {offset}-{end}/{size}",
                                           "Content-Length": str(len(chunk))})
            if resp.status_code in (200, 201):
                return resp.json()["id"]
            ranges = (resp.json() or {}).get("nextExpectedRanges") or []
            offset = int(ranges[0].split("-")[0]) if ranges else end + 1
            data.seek(offset)
        raise ApiError(0, "Upload ended before the server confirmed it", url)

    def share(self, item_id: str, perm: Permission) -> None:
        role = "write" if perm.role in ("write", "owner") else "read"
        if perm.kind in ("anyone", "domain"):
            self.c.json("POST", f"{GRAPH}/drives/{self.drive_id}/items/{item_id}/createLink",
                        json={"type": "edit" if role == "write" else "view",
                              "scope": "anonymous" if perm.kind == "anyone" else "organization"})
            return
        self.c.json("POST", f"{GRAPH}/drives/{self.drive_id}/items/{item_id}/invite",
                    json={"recipients": [{"email": perm.email}], "roles": [role],
                          "requireSignIn": True, "sendInvitation": False})


class GraphSites:
    """SharePoint sites and Microsoft 365 groups, for shared drive migrations."""

    def __init__(self, client: Client) -> None:
        self.c = client

    def search(self, query: str = "*") -> list[Drive]:
        out = []
        for s in self.c.pages(f"{GRAPH}/sites", "value", {"search": query or "*"},
                              next_key="@odata.nextLink"):
            out.append(Drive("", s.get("displayName") or s.get("name", ""), s["id"],
                             s.get("webUrl", "")))
        return out

    def libraries(self, site_id: str) -> list[Drive]:
        return [Drive(d["id"], d.get("name", ""), site_id, d.get("webUrl", ""))
                for d in self.c.pages(f"{GRAPH}/sites/{site_id}/drives", "value",
                                      next_key="@odata.nextLink")
                if d.get("driveType") == "documentLibrary"]

    def all_libraries(self) -> list[Drive]:
        out = []
        for site in self.search("*"):
            for lib in self.libraries(site.site_id):
                lib.name = site.name if lib.name in ("Documents", "Shared Documents") else \
                    f"{site.name} — {lib.name}"
                out.append(lib)
        return out

    def members(self, drive_id: str) -> list[Permission]:
        """Owners and members of the Microsoft 365 group behind a library, if any."""
        drive = self.c.json("GET", f"{GRAPH}/drives/{drive_id}", params={"$select": "owner"})
        group_id = ((drive.get("owner") or {}).get("group") or {}).get("id")
        if not group_id:
            return []
        out = []
        for rel, role in (("owners", "owner"), ("members", "write")):
            for m in self.c.pages(f"{GRAPH}/groups/{group_id}/{rel}", "value",
                                  {"$select": "mail,userPrincipalName"},
                                  next_key="@odata.nextLink"):
                email = m.get("mail") or m.get("userPrincipalName")
                if email and not any(p.email == email for p in out):
                    out.append(Permission(email, role))
        return out

    def create_group_site(self, name: str, timeout: float = 600) -> Drive:
        nickname = re.sub(r"[^A-Za-z0-9]", "", name)[:40] or "migrated"
        nickname += uuid.uuid4().hex[:6]
        group = self.c.json("POST", f"{GRAPH}/groups", json={
            "displayName": name, "mailNickname": nickname, "groupTypes": ["Unified"],
            "mailEnabled": True, "securityEnabled": False, "visibility": "Private",
            "description": "Created by Mail Migrator for a migrated shared drive"})
        deadline = time.monotonic() + timeout
        while True:  # the SharePoint site is provisioned a few minutes after the group
            try:
                drive = self.c.json("GET", f"{GRAPH}/groups/{group['id']}/drive")
                return Drive(drive["id"], name, "", drive.get("webUrl", ""))
            except ApiError as exc:
                if exc.status not in (400, 404) or time.monotonic() > deadline:
                    raise
                self.c._wait(15)

    def add_member(self, drive_id: str, perm: Permission) -> None:
        drive = self.c.json("GET", f"{GRAPH}/drives/{drive_id}", params={"$select": "owner"})
        group_id = ((drive.get("owner") or {}).get("group") or {}).get("id")
        if not group_id or perm.kind != "user" or not perm.email:
            # Not a group site: share the library root instead.
            root = self.c.json("GET", f"{GRAPH}/drives/{drive_id}/root", params={"$select": "id"})
            GraphDrive(self.c, drive_id).share(root["id"], perm)
            return
        user = self.c.json("GET", f"{GRAPH}/users/{quote(perm.email)}", params={"$select": "id"})
        ref = {"@odata.id": f"{GRAPH}/directoryObjects/{user['id']}"}
        rels = ["members"] + (["owners"] if perm.role == "owner" else [])
        for rel in rels:
            try:
                self.c.json("POST", f"{GRAPH}/groups/{group_id}/{rel}/$ref", json=ref)
            except ApiError as exc:
                if exc.status != 400 or "already exist" not in exc.message:
                    raise


class GraphDirectory:
    def __init__(self, client: Client) -> None:
        self.c = client

    def users(self) -> list[tuple[str, str]]:
        out = []
        for u in self.c.pages(f"{GRAPH}/users", "value",
                              {"$select": "displayName,mail,userPrincipalName,accountEnabled",
                               "$top": 999}, next_key="@odata.nextLink"):
            if u.get("accountEnabled") is False:
                continue
            email = u.get("mail") or u.get("userPrincipalName")
            if email and "#EXT#" not in email:
                out.append((email, u.get("displayName", "")))
        return sorted(out)
