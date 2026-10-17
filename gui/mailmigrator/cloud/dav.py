"""Standards-based sources: CardDAV contacts, CalDAV calendars, WebDAV files.

Covers iCloud, Fastmail, Zoho, Nextcloud/ownCloud, Synology, Radicale and
most other mail hosts that are not Google or Microsoft.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import BinaryIO, Iterator
from urllib.parse import unquote, urljoin, urlparse

import requests

from .http import ApiError, Client
from .models import Address, Attendee, Calendar, Contact, Event, FileItem

NS = {"d": "DAV:", "card": "urn:ietf:params:xml:ns:carddav",
      "cal": "urn:ietf:params:xml:ns:caldav", "cs": "http://calendarserver.org/ns/"}


def _client(user: str, password: str, stop=None) -> Client:
    session = requests.Session()
    session.auth = (user, password)
    return Client(None, stop=stop, session=session)


class Dav:
    def __init__(self, url: str, user: str, password: str, stop=None) -> None:
        if url and "://" not in url:
            url = "https://" + url
        self.url = url.rstrip("/") + "/"
        self.c = _client(user, password, stop)

    def propfind(self, url: str, body: str, depth: str = "0") -> ET.Element:
        resp = self.c.request("PROPFIND", url, auth=False, data=body.encode(),
                              headers={"Depth": depth,
                                       "Content-Type": "application/xml; charset=utf-8"})
        return ET.fromstring(resp.content)

    def report(self, url: str, body: str, depth: str = "1") -> ET.Element:
        resp = self.c.request("REPORT", url, auth=False, data=body.encode(),
                              headers={"Depth": depth,
                                       "Content-Type": "application/xml; charset=utf-8"})
        return ET.fromstring(resp.content)

    def _href(self, el: ET.Element | None, base: str) -> str:
        if el is None or el.text is None:
            return ""
        return urljoin(base, el.text.strip())

    def principal(self) -> str:
        body = ('<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop>'
                '</d:propfind>')
        for start in (self.url, urljoin(self.url, "/.well-known/carddav"),
                      urljoin(self.url, "/.well-known/caldav")):
            try:
                root = self.propfind(start, body)
            except ApiError:
                continue
            href = self._href(root.find(".//d:current-user-principal/d:href", NS), start)
            if href:
                return href
        return self.url

    def homes(self, prop: str, ns: str) -> list[str]:
        principal = self.principal()
        body = (f'<d:propfind xmlns:d="DAV:" xmlns:x="{NS[ns]}"><d:prop><x:{prop}/></d:prop>'
                '</d:propfind>')
        try:
            root = self.propfind(principal, body)
        except ApiError:
            return [self.url]
        homes = [self._href(h, principal) for h in root.findall(f".//{ns}:{prop}/d:href", NS)]
        return [h for h in homes if h] or [self.url]

    def collections(self, kind: str) -> list[tuple[str, str]]:
        """(url, name) of address books ('addressbook') or calendars ('calendar')."""
        ns = "card" if kind == "addressbook" else "cal"
        home_prop = "addressbook-home-set" if kind == "addressbook" else "calendar-home-set"
        body = ('<d:propfind xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">'
                '<d:prop><d:resourcetype/><d:displayname/>'
                '<cal:supported-calendar-component-set/></d:prop></d:propfind>')
        found: list[tuple[str, str]] = []
        candidates = [self.url] + [h for h in self.homes(home_prop, ns) if h != self.url]
        for home in candidates:
            try:
                root = self.propfind(home, body, depth="1")
            except ApiError:
                continue
            for resp in root.findall("d:response", NS):
                href = self._href(resp.find("d:href", NS), home)
                rtype = resp.find(".//d:resourcetype", NS)
                if rtype is None or rtype.find(f"{ns}:{kind}", NS) is None:
                    continue
                comps = [c.get("name") for c in resp.findall(".//cal:comp", NS)]
                if kind == "calendar" and comps and "VEVENT" not in comps:
                    continue  # task lists and journals
                name = (resp.findtext(".//d:displayname", "", NS) or
                        unquote(href.rstrip("/").rsplit("/", 1)[-1]))
                if href not in [u for u, _ in found]:
                    found.append((href, name))
            if found:
                break
        return found


# ======================================================================= vCard

def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _unescape(value: str) -> str:
    return (value.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",")
            .replace("\\;", ";").replace("\\\\", "\\"))


def _split(value: str, sep: str = ";") -> list[str]:
    parts, cur, esc = [], "", False
    for ch in value:
        if esc:
            cur += "\\" + ch
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == sep:
            parts.append(_unescape(cur))
            cur = ""
        else:
            cur += ch
    parts.append(_unescape(cur))
    return parts


def _vkind(params: dict[str, list[str]]) -> str:
    types = {t.lower() for v in params.get("TYPE", []) for t in v.split(",")}
    if types & {"cell", "mobile", "iphone"}:
        return "mobile"
    if "work" in types:
        return "work"
    if "home" in types:
        return "home"
    return "other"


def parse_vcards(text: str) -> list[Contact]:
    contacts: list[Contact] = []
    cur: Contact | None = None
    labels: dict[str, str] = {}
    for line in _unfold(text):
        if ":" not in line:
            continue
        head, value = line.split(":", 1)
        parts = head.split(";")
        name = parts[0].upper()
        group = ""
        if "." in name:
            group, name = name.split(".", 1)
        params: dict[str, list[str]] = {}
        for p in parts[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
                params.setdefault(k.upper(), []).append(v.strip('"'))
            else:  # vCard 2.1 bare types: TEL;CELL:
                params.setdefault("TYPE", []).append(p)
        if name == "BEGIN" and value.upper() == "VCARD":
            cur, labels = Contact(), {}
            continue
        if cur is None:
            continue
        if name == "END":
            if cur.label() != "(no name)" or cur.phones:
                contacts.append(cur)
            cur = None
        elif name == "UID":
            cur.source_id = value
        elif name == "FN":
            cur.display_name = _unescape(value)
        elif name == "N":
            n = _split(value) + [""] * 5
            cur.family_name, cur.given_name, cur.middle_name, cur.prefix, cur.suffix = n[:5]
        elif name == "NICKNAME":
            cur.nickname = _split(value, ",")[0]
        elif name == "EMAIL":
            cur.emails.append((_vkind(params), _unescape(value)))
        elif name == "TEL":
            cur.phones.append((_vkind(params), _unescape(value).replace("tel:", "")))
        elif name == "ADR":
            a = _split(value) + [""] * 7
            street = "\n".join(x for x in (a[1], a[2]) if x)
            addr = Address(_vkind(params), street, a[3], a[4], a[5], a[6])
            if not addr.empty():
                cur.addresses.append(addr)
        elif name == "ORG":
            o = _split(value) + [""]
            cur.company, cur.department = o[0], o[1]
        elif name == "TITLE":
            cur.job_title = _unescape(value)
        elif name == "BDAY":
            cur.birthday = _vdate(value)
        elif name == "NOTE":
            cur.notes = _unescape(value)
        elif name == "URL":
            cur.websites.append(_unescape(value))
        elif name == "CATEGORIES":
            cur.groups += [g for g in _split(value, ",") if g]
        elif name == "X-ABLABEL" and group:
            labels[group] = value
    return contacts


def _vdate(value: str) -> str:
    v = value.strip().split("T")[0]
    if v.startswith("--"):
        digits = v[2:].replace("-", "")
        return f"--{digits[:2]}-{digits[2:4]}" if len(digits) >= 4 else ""
    digits = v.replace("-", "")
    if len(digits) == 8 and digits.isdigit():
        if digits.startswith("1604"):
            return f"--{digits[4:6]}-{digits[6:8]}"
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return ""


class CardDavContacts:
    def __init__(self, url: str, user: str, password: str, stop=None) -> None:
        self.dav = Dav(url, user, password, stop)

    def list_contacts(self) -> Iterator[Contact]:
        body = ('<card:addressbook-query xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">'
                '<d:prop><d:getetag/><card:address-data/></d:prop></card:addressbook-query>')
        books = self.dav.collections("addressbook")
        if not books:
            raise ApiError(0, "No address books were found at this CardDAV address.")
        for url, name in books:
            root = self.dav.report(url, body)
            for resp in root.findall("d:response", NS):
                data = resp.findtext(".//card:address-data", "", NS)
                href = resp.findtext("d:href", "", NS)
                for c in parse_vcards(data):
                    c.source_id = c.source_id or href
                    if len(books) > 1 and name and name not in c.groups:
                        c.groups.append(name)
                    yield c


# ===================================================================== iCalendar

def _ical_time(prop) -> tuple[datetime | date | None, str]:
    if prop is None:
        return None, "UTC"
    value = prop.dt
    tz = "UTC"
    if isinstance(value, datetime):
        if value.tzinfo is None:
            tzid = prop.params.get("TZID")
            from .recurrence import zone
            value = value.replace(tzinfo=zone(tzid) if tzid else timezone.utc)
            tz = tzid or "UTC"
        else:
            tz = getattr(value.tzinfo, "key", None) or prop.params.get("TZID") or "UTC"
        from .recurrence import to_iana
        return value.astimezone(timezone.utc), to_iana(str(tz))
    return value, "UTC"


def parse_ics(text: str) -> list[Event]:
    from icalendar import Calendar as ICal

    try:
        cal = ICal.from_ical(text)
    except ValueError:
        return []
    out = []
    for comp in cal.walk("VEVENT"):
        start, tz = _ical_time(comp.get("DTSTART"))
        if start is None:
            continue
        end, _ = _ical_time(comp.get("DTEND"))
        if end is None and comp.get("DURATION") is not None:
            end = start + comp.get("DURATION").dt
        all_day = not isinstance(start, datetime)
        ev = Event(source_id=str(comp.get("UID", "")), uid=str(comp.get("UID", "")),
                   summary=str(comp.get("SUMMARY", "")),
                   description=str(comp.get("DESCRIPTION", "")),
                   location=str(comp.get("LOCATION", "")), start=start,
                   end=end or (start + timedelta(days=1) if all_day else start),
                   all_day=all_day, timezone=tz,
                   cancelled=str(comp.get("STATUS", "")).upper() == "CANCELLED",
                   private=str(comp.get("CLASS", "")).upper() in ("PRIVATE", "CONFIDENTIAL"),
                   free=str(comp.get("TRANSP", "")).upper() == "TRANSPARENT")
        for key in ("RRULE", "EXDATE", "RDATE"):
            values = comp.get(key)
            if values is None:
                continue
            for v in values if isinstance(values, list) else [values]:
                if key == "RRULE":
                    ev.recurrence.append("RRULE:" + v.to_ical().decode())
                else:
                    stamps = []
                    for d in v.dts:
                        value = d.dt
                        if isinstance(value, datetime):
                            if value.tzinfo is None:
                                from .recurrence import zone
                                value = value.replace(tzinfo=zone(tz))
                            stamps.append(value.astimezone(timezone.utc)
                                          .strftime("%Y%m%dT%H%M%SZ"))
                        else:
                            stamps.append(value.strftime("%Y%m%d"))
                    ev.recurrence.append(f"{key}:" + ",".join(stamps))
        rid = comp.get("RECURRENCE-ID")
        if rid is not None:
            ev.original_start, _ = _ical_time(rid)
            ev.source_id = f"{ev.uid}#{rid.to_ical().decode()}"
        org = comp.get("ORGANIZER")
        if org is not None:
            ev.organizer = str(org).replace("mailto:", "").replace("MAILTO:", "")
        attendees = comp.get("ATTENDEE")
        for a in (attendees if isinstance(attendees, list) else [attendees] if attendees else []):
            email = str(a).split(":", 1)[-1]
            if "@" not in email:
                continue
            part = str(a.params.get("PARTSTAT", "NEEDS-ACTION")).upper()
            ev.attendees.append(Attendee(email, str(a.params.get("CN", "")),
                                         {"ACCEPTED": "accepted", "DECLINED": "declined",
                                          "TENTATIVE": "tentative"}.get(part, "needsAction"),
                                         str(a.params.get("ROLE", "")).upper() == "OPT-PARTICIPANT"))
        for alarm in comp.walk("VALARM"):
            trig = alarm.get("TRIGGER")
            if trig is not None and isinstance(trig.dt, timedelta):
                ev.reminders.append(max(0, int(-trig.dt.total_seconds() // 60)))
        out.append(ev)
    return out


class CalDavCalendar:
    def __init__(self, url: str, user: str, password: str, stop=None) -> None:
        self.dav = Dav(url, user, password, stop)

    def owned_calendars(self) -> list[Calendar]:
        cals = self.dav.collections("calendar")
        return [Calendar(url, name, i == 0) for i, (url, name) in enumerate(cals)]

    list_calendars = owned_calendars

    def list_events(self, cal_id: str, since: datetime | None = None) -> Iterator[Event]:
        time_range = ""
        if since:
            time_range = (f'<cal:time-range start="{since.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"/>')
        body = ('<cal:calendar-query xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">'
                '<d:prop><d:getetag/><cal:calendar-data/></d:prop><cal:filter>'
                f'<cal:comp-filter name="VCALENDAR"><cal:comp-filter name="VEVENT">{time_range}'
                '</cal:comp-filter></cal:comp-filter></cal:filter></cal:calendar-query>')
        root = self.dav.report(cal_id, body)
        events: list[Event] = []
        for resp in root.findall("d:response", NS):
            data = resp.findtext(".//cal:calendar-data", "", NS)
            events += parse_ics(data)
        # Series masters first so their exceptions can be attached.
        events.sort(key=lambda e: e.is_exception)
        yield from events


# ======================================================================= WebDAV

class WebDavFiles:
    def __init__(self, url: str, user: str, password: str, stop=None) -> None:
        self.dav = Dav(url, user, password, stop)
        self.base = self.dav.url

    def root_id(self) -> str:
        return self.base

    def walk(self, folder_id: str | None = None, path: tuple[str, ...] = ()) -> Iterator[FileItem]:
        folder = folder_id or self.base
        body = ('<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/><d:getcontentlength/>'
                '<d:getlastmodified/><d:getcontenttype/></d:prop></d:propfind>')
        root = self.dav.propfind(folder, body, depth="1")
        items = []
        folder_path = urlparse(folder).path.rstrip("/")
        for resp in root.findall("d:response", NS):
            href = urljoin(folder, resp.findtext("d:href", "", NS))
            if urlparse(href).path.rstrip("/") == folder_path:
                continue
            is_dir = resp.find(".//d:resourcetype/d:collection", NS) is not None
            name = unquote(href.rstrip("/").rsplit("/", 1)[-1])
            modified = resp.findtext(".//d:getlastmodified", "", NS)
            try:
                mod = parsedate_to_datetime(modified) if modified else None
            except (TypeError, ValueError):
                mod = None
            items.append(FileItem(id=href, name=name, is_folder=is_dir,
                                  size=int(resp.findtext(".//d:getcontentlength", "0", NS) or 0),
                                  mime=resp.findtext(".//d:getcontenttype", "", NS),
                                  modified=mod, path=path))
        for item in sorted(items, key=lambda i: (not i.is_folder, i.name.lower())):
            yield item
            if item.is_folder:
                yield from self.walk(item.id, path + (item.name,))

    def download(self, item: FileItem, out: BinaryIO) -> int:
        resp = self.dav.c.request("GET", item.id, auth=False, stream=True)
        total = 0
        for chunk in resp.iter_content(1024 * 1024):
            self.dav.c.check_stop()
            out.write(chunk)
            total += len(chunk)
        return total

    def permissions(self, item_id: str) -> list:
        return []


def well_known(provider: str) -> dict[str, str]:
    """Default DAV addresses for providers that publish them."""
    return {
        "icloud": {"carddav": "https://contacts.icloud.com/", "caldav": "https://caldav.icloud.com/"},
        "fastmail": {"carddav": "https://carddav.fastmail.com/", "caldav": "https://caldav.fastmail.com/",
                     "webdav": "https://myfiles.fastmail.com/"},
        "zoho": {"carddav": "https://contacts.zoho.com/", "caldav": "https://calendar.zoho.com/"},
        "yahoo": {"carddav": "https://carddav.address.yahoo.com/",
                  "caldav": "https://caldav.calendar.yahoo.com/"},
        "aol": {"carddav": "https://carddav.aol.com/", "caldav": "https://caldav.aol.com/"},
        "gmx": {"carddav": "https://carddav.gmx.net/", "caldav": "https://caldav.gmx.net/"},
        "yandex": {"carddav": "https://carddav.yandex.ru/", "caldav": "https://caldav.yandex.ru/"},
    }.get(provider, {})

