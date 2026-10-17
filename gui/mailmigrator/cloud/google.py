"""Google Workspace / Gmail: contacts (People API), Calendar, Drive and Directory."""

from __future__ import annotations

import io
import json
import uuid
from datetime import date, datetime, timezone
from typing import BinaryIO, Iterator
from urllib.parse import quote

from .http import ApiError, Client
from .models import Address, Attendee, Calendar, Contact, Drive, Event, FileItem, Permission
from .recurrence import parse_local

PEOPLE = "https://people.googleapis.com/v1"
CAL = "https://www.googleapis.com/calendar/v3"
DRIVE = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"
DIRECTORY = "https://admin.googleapis.com/admin/directory/v1"

FOLDER = "application/vnd.google-apps.folder"
SHORTCUT = "application/vnd.google-apps.shortcut"
NATIVE_EXPORT = {
    "application/vnd.google-apps.document":
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "application/vnd.google-apps.spreadsheet":
        ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "application/vnd.google-apps.presentation":
        ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
    "application/vnd.google-apps.drawing": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.jam": ("application/pdf", ".pdf"),
}
NOT_EXPORTABLE = {
    "application/vnd.google-apps.form": "Google Forms cannot be exported",
    "application/vnd.google-apps.site": "Google Sites cannot be exported",
    "application/vnd.google-apps.map": "Google My Maps cannot be exported",
    "application/vnd.google-apps.fusiontable": "Fusion Tables are retired",
    "application/vnd.google-apps.script": "Apps Script projects are not files",
    SHORTCUT: "Shortcut to another item",
}
# Office -> Google conversion when "convert to Google formats" is chosen.
TO_NATIVE = {
    ".docx": "application/vnd.google-apps.document", ".doc": "application/vnd.google-apps.document",
    ".xlsx": "application/vnd.google-apps.spreadsheet", ".xls": "application/vnd.google-apps.spreadsheet",
    ".pptx": "application/vnd.google-apps.presentation", ".ppt": "application/vnd.google-apps.presentation",
}
ROLE_FROM_GOOGLE = {"reader": "read", "commenter": "comment", "writer": "write",
                    "fileOrganizer": "write", "organizer": "owner", "owner": "owner"}
ROLE_TO_GOOGLE = {"read": "reader", "comment": "commenter", "write": "writer", "owner": "writer"}
DRIVE_ROLE_TO_GOOGLE = {"read": "reader", "comment": "commenter", "write": "writer",
                        "owner": "organizer"}
SIMPLE_UPLOAD_LIMIT = 5 * 1024 * 1024
CHUNK = 8 * 256 * 1024 * 4   # 8 MiB, a multiple of 256 KiB as Google requires


def _kind(value: str) -> str:
    value = (value or "").lower()
    if value in ("home", "work", "mobile"):
        return value
    if value in ("cell", "iphone"):
        return "mobile"
    return "other"


def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# =================================================================== contacts

PERSON_FIELDS = ("names,nicknames,emailAddresses,phoneNumbers,addresses,organizations,"
                 "birthdays,biographies,urls,memberships")


def person_to_contact(p: dict, group_names: dict[str, str]) -> Contact:
    c = Contact(source_id=p.get("resourceName", ""))
    if p.get("names"):
        n = p["names"][0]
        c.display_name = n.get("displayName", "")
        c.given_name, c.family_name = n.get("givenName", ""), n.get("familyName", "")
        c.middle_name = n.get("middleName", "")
        c.prefix, c.suffix = n.get("honorificPrefix", ""), n.get("honorificSuffix", "")
    if p.get("nicknames"):
        c.nickname = p["nicknames"][0].get("value", "")
    c.emails = [(_kind(e.get("type")), e["value"]) for e in p.get("emailAddresses", [])
                if e.get("value")]
    c.phones = [(_kind(e.get("type")), e["value"]) for e in p.get("phoneNumbers", [])
                if e.get("value")]
    for a in p.get("addresses", []):
        addr = Address(_kind(a.get("type")), a.get("streetAddress", ""), a.get("city", ""),
                       a.get("region", ""), a.get("postalCode", ""), a.get("country", ""))
        if addr.empty() and a.get("formattedValue"):
            addr.street = a["formattedValue"]
        if not addr.empty():
            c.addresses.append(addr)
    if p.get("organizations"):
        o = p["organizations"][0]
        c.company, c.job_title = o.get("name", ""), o.get("title", "")
        c.department = o.get("department", "")
    for b in p.get("birthdays", []):
        d = b.get("date") or {}
        if d.get("month") and d.get("day"):
            c.birthday = (f"{d['year']:04d}-" if d.get("year") else "--") + \
                f"{d['month']:02d}-{d['day']:02d}"
            break
    if p.get("biographies"):
        c.notes = p["biographies"][0].get("value", "")
    c.websites = [u["value"] for u in p.get("urls", []) if u.get("value")]
    for m in p.get("memberships", []):
        rn = (m.get("contactGroupMembership") or {}).get("contactGroupResourceName", "")
        if rn in group_names:
            c.groups.append(group_names[rn])
    return c


def contact_to_person(c: Contact, group_ids: dict[str, str]) -> dict:
    p: dict = {}
    names = {k: v for k, v in (("givenName", c.given_name), ("familyName", c.family_name),
                               ("middleName", c.middle_name), ("honorificPrefix", c.prefix),
                               ("honorificSuffix", c.suffix)) if v}
    if not names and c.display_name:
        names["unstructuredName"] = c.display_name
    if names:
        p["names"] = [names]
    if c.nickname:
        p["nicknames"] = [{"value": c.nickname}]
    if c.emails:
        p["emailAddresses"] = [{"value": v, "type": k} for k, v in c.emails]
    if c.phones:
        p["phoneNumbers"] = [{"value": v, "type": k} for k, v in c.phones]
    if c.addresses:
        p["addresses"] = [{"type": a.kind, "streetAddress": a.street, "city": a.city,
                           "region": a.region, "postalCode": a.postal_code,
                           "country": a.country} for a in c.addresses]
    if c.company or c.job_title or c.department:
        p["organizations"] = [{k: v for k, v in (("name", c.company), ("title", c.job_title),
                                                 ("department", c.department)) if v}]
    if c.birthday:
        parts = c.birthday.lstrip("-").split("-")
        try:
            if c.birthday.startswith("--"):
                p["birthdays"] = [{"date": {"month": int(parts[0]), "day": int(parts[1])}}]
            else:
                p["birthdays"] = [{"date": {"year": int(parts[0]), "month": int(parts[1]),
                                            "day": int(parts[2])}}]
        except (ValueError, IndexError):
            pass
    if c.notes:
        p["biographies"] = [{"value": c.notes, "contentType": "TEXT_PLAIN"}]
    if c.websites:
        p["urls"] = [{"value": u} for u in c.websites]
    memberships = [{"contactGroupMembership": {"contactGroupResourceName": group_ids[g]}}
                   for g in c.groups if g in group_ids]
    if memberships:
        p["memberships"] = memberships
    return p


class GoogleContacts:
    def __init__(self, client: Client) -> None:
        self.c = client
        self._groups: dict[str, str] | None = None   # resourceName -> name

    def groups(self) -> dict[str, str]:
        if self._groups is None:
            self._groups = {}
            for g in self.c.pages(f"{PEOPLE}/contactGroups", "contactGroups",
                                  {"pageSize": 1000}):
                if g.get("groupType") == "USER_CONTACT_GROUP":
                    self._groups[g["resourceName"]] = g.get("name") or g.get("formattedName", "")
        return self._groups

    def list_contacts(self) -> Iterator[Contact]:
        groups = self.groups()
        for p in self.c.pages(f"{PEOPLE}/people/me/connections", "connections",
                              {"personFields": PERSON_FIELDS, "pageSize": 1000}):
            yield person_to_contact(p, groups)

    def ensure_groups(self, names: set[str]) -> dict[str, str]:
        existing = {name: rn for rn, name in self.groups().items()}
        for name in sorted(n for n in names if n and n not in existing):
            created = self.c.json("POST", f"{PEOPLE}/contactGroups",
                                  json={"contactGroup": {"name": name}})
            existing[name] = created["resourceName"]
            self._groups[created["resourceName"]] = name  # type: ignore[index]
        return existing

    def create_contacts(self, contacts: list[Contact]) -> list[str]:
        group_ids = self.ensure_groups({g for c in contacts for g in c.groups})
        out: list[str] = []
        for i in range(0, len(contacts), 200):
            batch = contacts[i:i + 200]
            body = {"contacts": [{"contactPerson": contact_to_person(c, group_ids)}
                                 for c in batch], "readMask": "names"}
            data = self.c.json("POST", f"{PEOPLE}/people:batchCreateContacts", json=body)
            created = data.get("createdPeople", [])
            for entry in created:
                out.append((entry.get("person") or {}).get("resourceName", ""))
        return out

    def update_contact(self, resource: str, contact: Contact) -> None:
        group_ids = self.ensure_groups(set(contact.groups))
        current = self.c.json("GET", f"{PEOPLE}/{resource}", params={"personFields": "names"})
        body = contact_to_person(contact, group_ids)
        body["etag"] = current.get("etag")
        fields = ",".join(k for k in ("names", "nicknames", "emailAddresses", "phoneNumbers",
                                      "addresses", "organizations", "birthdays", "biographies",
                                      "urls", "memberships"))
        for key in fields.split(","):
            body.setdefault(key, [])
        self.c.json("PATCH", f"{PEOPLE}/{resource}:updateContact",
                    params={"updatePersonFields": fields}, json=body)


# =================================================================== calendar

SKIP_EVENT_TYPES = {"workingLocation", "birthday", "fromGmail"}


def _g_time(t: dict | None) -> tuple[datetime | date | None, bool, str]:
    if not t:
        return None, False, "UTC"
    if "date" in t:
        return date.fromisoformat(t["date"]), True, t.get("timeZone", "UTC")
    tz = t.get("timeZone", "UTC")
    return parse_local(t["dateTime"], tz), False, tz


def g_event_to_event(e: dict, cal_tz: str = "UTC") -> Event | None:
    if e.get("eventType") in SKIP_EVENT_TYPES:
        return None
    if e.get("status") == "cancelled" and not e.get("recurringEventId"):
        return None  # a deleted event (listed because showDeleted is on)
    start, all_day, tz = _g_time(e.get("start"))
    end, _, _ = _g_time(e.get("end"))
    ev = Event(source_id=e["id"], uid=e.get("iCalUID", ""), summary=e.get("summary", ""),
               description=e.get("description", ""), location=e.get("location", ""),
               start=start, end=end, all_day=all_day,
               timezone=(e.get("start") or {}).get("timeZone") or cal_tz,
               recurrence=list(e.get("recurrence", [])),
               cancelled=e.get("status") == "cancelled",
               organizer=(e.get("organizer") or {}).get("email", ""),
               private=e.get("visibility") in ("private", "confidential"),
               free=e.get("transparency") == "transparent", html="<" in e.get("description", ""))
    if e.get("originalStartTime"):
        ev.original_start, _, _ = _g_time(e["originalStartTime"])
    for a in e.get("attendees", []):
        if a.get("resource") or not a.get("email"):
            continue
        ev.attendees.append(Attendee(a["email"], a.get("displayName", ""),
                                     a.get("responseStatus", "needsAction"),
                                     bool(a.get("optional"))))
    rem = e.get("reminders") or {}
    ev.reminders = [o["minutes"] for o in rem.get("overrides", []) if "minutes" in o]
    if rem.get("useDefault") and not ev.reminders:
        ev.reminders = [10]
    return ev


def event_to_g(ev: Event, include_attendees: bool = True) -> dict:
    def when(value, tz):
        if isinstance(value, datetime):
            return {"dateTime": _rfc3339(value), "timeZone": tz or "UTC"}
        return {"date": value.isoformat()}

    body: dict = {"iCalUID": ev.uid or f"{uuid.uuid4()}@mailmigrator",
                  "summary": ev.summary, "description": ev.description,
                  "location": ev.location, "start": when(ev.start, ev.timezone),
                  "end": when(ev.end or ev.start, ev.timezone)}
    if ev.recurrence and not ev.is_exception:
        body["recurrence"] = ev.recurrence
    if ev.original_start is not None:
        body["originalStartTime"] = when(ev.original_start, ev.timezone)
    if ev.cancelled:
        body["status"] = "cancelled"
    if ev.organizer:
        body["organizer"] = {"email": ev.organizer}
    if include_attendees and ev.attendees:
        body["attendees"] = [{"email": a.email, "displayName": a.name or None,
                              "responseStatus": a.response, "optional": a.optional}
                             for a in ev.attendees]
    body["reminders"] = ({"useDefault": False,
                          "overrides": [{"method": "popup", "minutes": m}
                                        for m in ev.reminders[:5]]}
                         if ev.reminders else {"useDefault": False})
    if ev.private:
        body["visibility"] = "private"
    if ev.free:
        body["transparency"] = "transparent"
    return body


class GoogleCalendar:
    def __init__(self, client: Client) -> None:
        self.c = client

    def list_calendars(self) -> list[Calendar]:
        out = []
        for c in self.c.pages(f"{CAL}/users/me/calendarList", "items", {"maxResults": 250}):
            out.append(Calendar(c["id"], c.get("summaryOverride") or c.get("summary", ""),
                                bool(c.get("primary")),
                                c.get("accessRole") not in ("owner", "writer"),
                                c.get("backgroundColor", "")))
        return out

    def owned_calendars(self) -> list[Calendar]:
        return [c for c in self.list_calendars() if not c.read_only]

    def list_events(self, cal_id: str, since: datetime | None = None) -> Iterator[Event]:
        meta = self.c.json("GET", f"{CAL}/calendars/{quote(cal_id, safe='')}")
        params = {"maxResults": 2500, "showDeleted": "true", "singleEvents": "false"}
        if since:
            params["timeMin"] = _rfc3339(since)
        for e in self.c.pages(f"{CAL}/calendars/{quote(cal_id, safe='')}/events", "items",
                              params):
            ev = g_event_to_event(e, meta.get("timeZone", "UTC"))
            if ev:
                yield ev

    def ensure_calendar(self, name: str, primary: bool) -> str:
        if primary:
            return "primary"
        for c in self.owned_calendars():
            if c.name == name:
                return c.id
        return self.c.json("POST", f"{CAL}/calendars", json={"summary": name})["id"]

    def import_event(self, cal_id: str, ev: Event, include_attendees: bool = True,
                     master_dest_id: str | None = None) -> str:
        # events.import never sends invitations or updates to attendees.
        body = event_to_g(ev, include_attendees)
        data = self.c.json("POST", f"{CAL}/calendars/{quote(cal_id, safe='')}/events/import",
                           params={"conferenceDataVersion": 0}, json=body)
        return data.get("id", "")

    def delete_event(self, cal_id: str, event_id: str) -> None:
        try:
            self.c.request("DELETE", f"{CAL}/calendars/{quote(cal_id, safe='')}/events/"
                                     f"{event_id}", params={"sendUpdates": "none"})
        except ApiError as exc:
            if exc.status not in (404, 410):
                raise


# =================================================================== drive

def g_file(f: dict, path: tuple[str, ...] = ()) -> FileItem:
    mime = f.get("mimeType", "")
    item = FileItem(id=f["id"], name=f.get("name", ""), is_folder=mime == FOLDER,
                    size=int(f.get("size") or 0), mime=mime,
                    modified=_parse_time(f.get("modifiedTime")), drive_id=f.get("driveId", ""),
                    path=path)
    if mime in NATIVE_EXPORT:
        item.native = mime
        item.export_ext = NATIVE_EXPORT[mime][1]
    elif mime in NOT_EXPORTABLE:
        item.skip_reason = NOT_EXPORTABLE[mime]
    elif mime.startswith("application/vnd.google-apps.") and mime != FOLDER:
        item.skip_reason = f"Google item type {mime.rsplit('.', 1)[-1]} cannot be downloaded"
    return item


FILE_FIELDS = "id,name,mimeType,size,modifiedTime,md5Checksum,driveId,parents"


class GoogleDrive:
    """My Drive or a shared drive, as a file source and as a destination."""

    def __init__(self, client: Client, drive_id: str = "") -> None:
        self.c = client
        self.drive_id = drive_id
        self._folders: dict[tuple[str, ...], str] = {}

    def root_id(self) -> str:
        return self.drive_id or "root"

    def _list(self, folder_id: str) -> Iterator[dict]:
        params = {"q": f"'{folder_id}' in parents and trashed = false",
                  "fields": f"nextPageToken,files({FILE_FIELDS})", "pageSize": 1000,
                  "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
        if self.drive_id:
            params.update(corpora="drive", driveId=self.drive_id)
        yield from self.c.pages(f"{DRIVE}/files", "files", params)

    def walk(self, folder_id: str | None = None, path: tuple[str, ...] = ()) -> Iterator[FileItem]:
        """Folders first, then their contents, depth first."""
        for f in sorted(self._list(folder_id or self.root_id()),
                        key=lambda f: (f.get("mimeType") != FOLDER, f.get("name", "").lower())):
            item = g_file(f, path)
            yield item
            if item.is_folder:
                yield from self.walk(item.id, path + (item.name,))

    def download(self, item: FileItem, out: BinaryIO) -> int:
        if item.native:
            mime = NATIVE_EXPORT[item.native][0]
            resp = self.c.request("GET", f"{DRIVE}/files/{item.id}/export",
                                  params={"mimeType": mime}, stream=True)
        else:
            resp = self.c.request("GET", f"{DRIVE}/files/{item.id}",
                                  params={"alt": "media", "supportsAllDrives": "true",
                                          "acknowledgeAbuse": "false"}, stream=True)
        total = 0
        for chunk in resp.iter_content(1024 * 1024):
            self.c.check_stop()
            out.write(chunk)
            total += len(chunk)
        return total

    def permissions(self, item_id: str) -> list[Permission]:
        out = []
        params = {"fields": "nextPageToken,permissions(id,type,role,emailAddress,domain,"
                            "permissionDetails)", "supportsAllDrives": "true"}
        for p in self.c.pages(f"{DRIVE}/files/{item_id}/permissions", "permissions", params):
            details = p.get("permissionDetails") or []
            if details and all(d.get("inherited") for d in details):
                continue  # inherited from a parent folder or the shared drive
            role = ROLE_FROM_GOOGLE.get(p.get("role", ""), "read")
            if p.get("role") == "owner":
                continue  # ownership stays with the destination account
            out.append(Permission(p.get("emailAddress", ""), role, p.get("type", "user"),
                                  p.get("domain", "")))
        return out

    # -- destination --

    def ensure_folder(self, path: tuple[str, ...], root: tuple[str, ...] = ()) -> str:
        full = root + path
        if not full:
            return self.root_id()
        if full in self._folders:
            return self._folders[full]
        parent = self.ensure_folder(full[:-1])
        name = full[-1]
        safe = name.replace("\\", "\\\\").replace("'", "\\'")
        params = {"q": f"'{parent}' in parents and name = '{safe}' and mimeType = '{FOLDER}' "
                       f"and trashed = false", "fields": "files(id)",
                  "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
        if self.drive_id:
            params.update(corpora="drive", driveId=self.drive_id)
        found = self.c.json("GET", f"{DRIVE}/files", params=params).get("files", [])
        if found:
            folder_id = found[0]["id"]
        else:
            folder_id = self.c.json("POST", f"{DRIVE}/files",
                                    params={"supportsAllDrives": "true"},
                                    json={"name": name, "mimeType": FOLDER,
                                          "parents": [parent]})["id"]
        self._folders[full] = folder_id
        return folder_id

    def existing(self, folder_id: str) -> dict[str, tuple[str, int]]:
        return {f.get("name", ""): (f["id"], int(f.get("size") or 0))
                for f in self._list(folder_id) if f.get("mimeType") != FOLDER}

    def upload(self, folder_id: str, name: str, data: BinaryIO, size: int,
               modified: datetime | None = None, convert: bool = False) -> str:
        meta: dict = {"name": name, "parents": [folder_id]}
        if modified:
            meta["modifiedTime"] = _rfc3339(modified)
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if convert and ext in TO_NATIVE:
            meta["mimeType"] = TO_NATIVE[ext]
            meta["name"] = name[: -len(ext)]
        params = {"supportsAllDrives": "true", "fields": "id"}
        if size <= SIMPLE_UPLOAD_LIMIT:
            boundary = "mm" + uuid.uuid4().hex
            data.seek(0)
            body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
                    f"{json.dumps(meta)}\r\n--{boundary}\r\n"
                    f"Content-Type: application/octet-stream\r\n\r\n").encode() + \
                data.read() + f"\r\n--{boundary}--".encode()
            resp = self.c.json("POST", f"{UPLOAD}/files",
                               params=dict(params, uploadType="multipart"), data=body,
                               headers={"Content-Type": f"multipart/related; boundary={boundary}"})
            return resp["id"]
        start = self.c.request("POST", f"{UPLOAD}/files",
                               params=dict(params, uploadType="resumable"), json=meta,
                               headers={"X-Upload-Content-Length": str(size)})
        session_url = start.headers["Location"]
        offset = 0
        data.seek(0)
        while True:
            self.c.check_stop()
            chunk = data.read(CHUNK)
            end = offset + len(chunk) - 1
            resp = self.c.request("PUT", session_url, auth=False, data=io.BytesIO(chunk),
                                  headers={"Content-Range": f"bytes {offset}-{end}/{size}"},
                                  allow_redirects=False) if chunk else None
            if resp is None:
                raise ApiError(0, "Upload ended before the server confirmed it", session_url)
            if resp.status_code in (200, 201):
                return resp.json()["id"]
            if resp.status_code == 308:
                rng = resp.headers.get("Range")
                offset = int(rng.split("-")[1]) + 1 if rng else 0
                data.seek(offset)
                continue
            raise ApiError(resp.status_code, "Unexpected upload response", session_url)

    def share(self, item_id: str, perm: Permission) -> None:
        body: dict = {"type": perm.kind if perm.kind in ("user", "group", "domain", "anyone")
                      else "user",
                      "role": ROLE_TO_GOOGLE.get(perm.role, "reader")}
        if body["type"] in ("user", "group"):
            body["emailAddress"] = perm.email
        elif body["type"] == "domain":
            if not perm.domain:
                raise ApiError(0, "an “anyone in the organization” link has no domain to "
                                  "share with; add a domain rule to the user mapping")
            body["domain"] = perm.domain
        self.c.json("POST", f"{DRIVE}/files/{item_id}/permissions",
                    params={"sendNotificationEmail": "false", "supportsAllDrives": "true"},
                    json=body)


class GoogleSharedDrives:
    """Listing and creating shared drives (needs a Workspace admin)."""

    def __init__(self, client: Client, admin: bool) -> None:
        self.c = client
        self.admin = admin

    def list(self) -> list[Drive]:
        params = {"pageSize": 100}
        if self.admin:
            params["useDomainAdminAccess"] = "true"
        return [Drive(d["id"], d.get("name", ""))
                for d in self.c.pages(f"{DRIVE}/drives", "drives", params)]

    def members(self, drive_id: str) -> list[Permission]:
        params = {"supportsAllDrives": "true",
                  "fields": "nextPageToken,permissions(type,role,emailAddress,domain)"}
        if self.admin:
            params["useDomainAdminAccess"] = "true"
        out = []
        for p in self.c.pages(f"{DRIVE}/files/{drive_id}/permissions", "permissions", params):
            out.append(Permission(p.get("emailAddress", ""),
                                  ROLE_FROM_GOOGLE.get(p.get("role", ""), "read"),
                                  p.get("type", "user"), p.get("domain", "")))
        return out

    def create(self, name: str) -> Drive:
        data = self.c.json("POST", f"{DRIVE}/drives", params={"requestId": str(uuid.uuid4())},
                           json={"name": name})
        return Drive(data["id"], data.get("name", name))

    def find(self, name: str) -> Drive | None:
        for d in self.list():
            if d.name == name:
                return d
        return None

    def add_member(self, drive_id: str, perm: Permission) -> None:
        body = {"type": "user" if perm.kind not in ("group", "domain") else perm.kind,
                "role": DRIVE_ROLE_TO_GOOGLE.get(perm.role, "reader")}
        if body["type"] == "domain":
            body["domain"] = perm.domain
        else:
            body["emailAddress"] = perm.email
        params = {"supportsAllDrives": "true", "sendNotificationEmail": "false"}
        if self.admin:
            params["useDomainAdminAccess"] = "true"
        self.c.json("POST", f"{DRIVE}/files/{drive_id}/permissions", params=params, json=body)


# =================================================================== directory

class GoogleDirectory:
    def __init__(self, client: Client) -> None:
        self.c = client

    def users(self) -> list[tuple[str, str]]:
        out = []
        for u in self.c.pages(f"{DIRECTORY}/users", "users",
                              {"customer": "my_customer", "maxResults": 500,
                               "orderBy": "email"}):
            if not u.get("suspended") and not u.get("archived"):
                out.append((u["primaryEmail"], (u.get("name") or {}).get("fullName", "")))
        return out
