"""Provider-neutral contacts, events and files, so any source can feed any destination."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime


@dataclass
class Address:
    kind: str = "home"          # home, work, other
    street: str = ""
    city: str = ""
    region: str = ""
    postal_code: str = ""
    country: str = ""

    def empty(self) -> bool:
        return not any((self.street, self.city, self.region, self.postal_code, self.country))


@dataclass
class Contact:
    source_id: str = ""
    display_name: str = ""
    given_name: str = ""
    family_name: str = ""
    middle_name: str = ""
    prefix: str = ""
    suffix: str = ""
    nickname: str = ""
    emails: list[tuple[str, str]] = field(default_factory=list)    # (kind, address)
    phones: list[tuple[str, str]] = field(default_factory=list)    # (kind, number); kinds: mobile, home, work, other
    addresses: list[Address] = field(default_factory=list)
    company: str = ""
    job_title: str = ""
    department: str = ""
    birthday: str = ""          # YYYY-MM-DD, or --MM-DD when the year is unknown
    notes: str = ""
    websites: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)   # group / label names

    def label(self) -> str:
        return (self.display_name or " ".join(x for x in (self.given_name, self.family_name) if x)
                or (self.emails[0][1] if self.emails else "") or self.company or "(no name)")

    def fingerprint(self) -> str:
        data = asdict(self)
        data.pop("source_id", None)
        return hashlib.sha1(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class Attendee:
    email: str
    name: str = ""
    response: str = "needsAction"   # accepted, declined, tentative, needsAction
    optional: bool = False


@dataclass
class Event:
    source_id: str = ""
    uid: str = ""                   # iCalendar UID shared by a series and its exceptions
    summary: str = ""
    description: str = ""
    location: str = ""
    start: datetime | date | None = None   # aware datetime in UTC, or a date for all-day
    end: datetime | date | None = None
    all_day: bool = False
    timezone: str = "UTC"           # IANA name the event was created in
    recurrence: list[str] = field(default_factory=list)   # RRULE:/EXDATE:/RDATE: lines
    original_start: datetime | date | None = None  # set on a changed/cancelled occurrence
    cancelled: bool = False
    organizer: str = ""
    attendees: list[Attendee] = field(default_factory=list)
    reminders: list[int] = field(default_factory=list)    # minutes before start
    private: bool = False
    free: bool = False              # shows as free rather than busy
    html: bool = False              # description is HTML

    @property
    def is_exception(self) -> bool:
        return self.original_start is not None

    def fingerprint(self) -> str:
        data = asdict(self)
        data.pop("source_id", None)
        return hashlib.sha1(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class Calendar:
    id: str
    name: str
    primary: bool = False
    read_only: bool = False
    color: str = ""


@dataclass
class Permission:
    email: str = ""                 # empty for link/anyone/domain shares
    role: str = "read"              # read, comment, write, owner
    kind: str = "user"              # user, group, domain, anyone
    domain: str = ""


@dataclass
class FileItem:
    id: str
    name: str
    is_folder: bool = False
    size: int = 0
    mime: str = ""
    modified: datetime | None = None
    native: str = ""                # Google-native type (document, spreadsheet, ...) needing export
    export_ext: str = ""            # extension added on export, e.g. ".docx"
    skip_reason: str = ""           # set when the item cannot be copied at all
    drive_id: str = ""              # owning drive (shared drives / SharePoint libraries)
    path: tuple[str, ...] = ()      # parent folder names from the migration root

    @property
    def target_name(self) -> str:
        if self.export_ext and not self.name.lower().endswith(self.export_ext):
            return self.name + self.export_ext
        return self.name


@dataclass
class Drive:
    """A Google shared drive or a SharePoint/OneDrive document library."""
    id: str
    name: str
    site_id: str = ""
    web_url: str = ""
    members: list[Permission] = field(default_factory=list)


@dataclass
class Stats:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    bytes: int = 0
    errors: list[str] = field(default_factory=list)

    def error(self, message: str) -> None:
        self.failed += 1
        if len(self.errors) < 200:
            self.errors.append(message)

    def summary(self, noun: str) -> str:
        if self.created == 1 and noun.endswith("s"):
            noun = noun[:-1]
        parts = [f"{self.created:,} {noun} copied"]
        if self.updated:
            parts.append(f"{self.updated:,} updated")
        if self.skipped:
            parts.append(f"{self.skipped:,} already there")
        if self.failed:
            parts.append(f"{self.failed:,} failed")
        return ", ".join(parts)


def iso(value: datetime | date | None) -> str:
    if value is None:
        return ""
    return value.isoformat()
