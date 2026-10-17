"""Recurring events: iCalendar RRULE <-> Microsoft Graph patternedRecurrence,
and Windows <-> IANA time zone names."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAYS = ["SU", "MO", "TU", "WE", "TH", "FR", "SA"]
GRAPH_DAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
INDEX = {1: "first", 2: "second", 3: "third", 4: "fourth", -1: "last"}
INDEX_NUM = {v: k for k, v in INDEX.items()}


class Unsupported(ValueError):
    """The rule has no exact Graph equivalent (the caller expands occurrences instead)."""


def parse_rrule(line: str) -> dict[str, str]:
    body = line.split(":", 1)[1] if line.upper().startswith("RRULE:") else line
    parts = {}
    for item in body.split(";"):
        if "=" in item:
            k, v = item.split("=", 1)
            parts[k.upper()] = v.upper() if k.upper() != "UNTIL" else v
    return parts


def _parse_until(value: str) -> date:
    value = value.strip()
    if "T" in value:
        dt = datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
        return dt.date()
    return datetime.strptime(value[:8], "%Y%m%d").date()


def _split_byday(value: str) -> list[tuple[int | None, str]]:
    out = []
    for token in value.split(","):
        token = token.strip()
        day = token[-2:]
        num = token[:-2]
        out.append((int(num) if num not in ("", "+") else None, day))
    return out


def rrule_to_graph(rrule: str, start: date, tz: str) -> dict:
    """Return Graph's ``recurrence`` object for an RRULE line."""
    r = parse_rrule(rrule)
    freq = r.get("FREQ", "")
    interval = int(r.get("INTERVAL", "1") or 1)
    unsupported = {"BYHOUR", "BYMINUTE", "BYSECOND", "BYYEARDAY", "BYWEEKNO"} & set(r)
    if unsupported or freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        raise Unsupported(rrule)
    pattern: dict = {"interval": interval}
    byday = _split_byday(r["BYDAY"]) if "BYDAY" in r else []
    setpos = int(r["BYSETPOS"]) if "BYSETPOS" in r and "," not in r["BYSETPOS"] else None

    if freq == "DAILY":
        if byday:  # e.g. every weekday, expressed as weekly
            pattern.update(type="weekly", daysOfWeek=[GRAPH_DAYS[DAYS.index(d)] for _, d in byday],
                           firstDayOfWeek="sunday")
        else:
            pattern["type"] = "daily"
    elif freq == "WEEKLY":
        days = [d for _, d in byday] or [DAYS[(start.weekday() + 1) % 7]]
        pattern.update(type="weekly", daysOfWeek=[GRAPH_DAYS[DAYS.index(d)] for d in days],
                       firstDayOfWeek=GRAPH_DAYS[DAYS.index(r.get("WKST", "SU"))])
    elif freq in ("MONTHLY", "YEARLY"):
        relative = byday and (setpos is not None or byday[0][0] is not None)
        if relative:
            nums = {n for n, _ in byday}
            index = setpos if setpos is not None else byday[0][0]
            if index not in INDEX or (setpos is None and len(nums) > 1):
                raise Unsupported(rrule)
            pattern.update(type="relativeMonthly" if freq == "MONTHLY" else "relativeYearly",
                           daysOfWeek=[GRAPH_DAYS[DAYS.index(d)] for _, d in byday],
                           index=INDEX[index])
        elif byday:
            raise Unsupported(rrule)  # e.g. every Monday of the month
        else:
            day = r.get("BYMONTHDAY", str(start.day))
            if "," in day or int(day) < 1:
                raise Unsupported(rrule)
            pattern.update(type="absoluteMonthly" if freq == "MONTHLY" else "absoluteYearly",
                           dayOfMonth=int(day))
        if freq == "YEARLY":
            month = r.get("BYMONTH", str(start.month))
            if "," in month:
                raise Unsupported(rrule)
            pattern["month"] = int(month)
            pattern["interval"] = interval  # Graph yearly interval is in years

    rng: dict = {"startDate": start.isoformat(), "recurrenceTimeZone": tz}
    if "COUNT" in r:
        rng.update(type="numbered", numberOfOccurrences=int(r["COUNT"]))
    elif "UNTIL" in r:
        rng.update(type="endDate", endDate=_parse_until(r["UNTIL"]).isoformat())
    else:
        rng["type"] = "noEnd"
    return {"pattern": pattern, "range": rng}


def graph_to_rrule(rec: dict) -> str:
    p, rng = rec.get("pattern") or {}, rec.get("range") or {}
    kind = p.get("type", "")
    interval = int(p.get("interval") or 1)
    parts = []
    days = ",".join(DAYS[GRAPH_DAYS.index(d.lower())] for d in p.get("daysOfWeek") or [])
    if kind == "daily":
        parts.append("FREQ=DAILY")
    elif kind == "weekly":
        parts += ["FREQ=WEEKLY", f"BYDAY={days}"] if days else ["FREQ=WEEKLY"]
        first = (p.get("firstDayOfWeek") or "sunday").lower()
        parts.append("WKST=" + DAYS[GRAPH_DAYS.index(first)])
    elif kind in ("absoluteMonthly", "absoluteYearly"):
        parts.append("FREQ=MONTHLY" if kind == "absoluteMonthly" else "FREQ=YEARLY")
        parts.append(f"BYMONTHDAY={int(p.get('dayOfMonth') or 1)}")
        if kind == "absoluteYearly":
            parts.append(f"BYMONTH={int(p.get('month') or 1)}")
    elif kind in ("relativeMonthly", "relativeYearly"):
        parts.append("FREQ=MONTHLY" if kind == "relativeMonthly" else "FREQ=YEARLY")
        parts.append(f"BYDAY={days}")
        parts.append(f"BYSETPOS={INDEX_NUM.get((p.get('index') or 'first').lower(), 1)}")
        if kind == "relativeYearly":
            parts.append(f"BYMONTH={int(p.get('month') or 1)}")
    else:
        raise Unsupported(str(rec))
    if interval != 1:
        parts.append(f"INTERVAL={interval}")
    rtype = rng.get("type")
    if rtype == "numbered":
        parts.append(f"COUNT={int(rng.get('numberOfOccurrences') or 1)}")
    elif rtype == "endDate" and rng.get("endDate"):
        end = date.fromisoformat(rng["endDate"][:10])
        parts.append("UNTIL=" + end.strftime("%Y%m%d") + "T235959Z")
    return "RRULE:" + ";".join(parts)


def expand(rrule: str, start: datetime | date, until: datetime | date,
           limit: int = 750) -> list[datetime | date]:
    """Occurrence starts for a rule (used when Graph cannot express it)."""
    from dateutil.rrule import rrulestr

    body = rrule.split(":", 1)[1] if rrule.upper().startswith("RRULE:") else rrule
    dtstart = start if isinstance(start, datetime) else datetime(start.year, start.month,
                                                                 start.day)
    rule = rrulestr(_normalize_until(body, dtstart), dtstart=dtstart)
    out = []
    for occ in rule:
        if isinstance(until, datetime):
            stop = occ > until.replace(tzinfo=occ.tzinfo) if occ.tzinfo is None else occ > until
        else:
            stop = occ.date() > until if isinstance(occ, datetime) else occ > until
        if stop or len(out) >= limit:
            break
        out.append(occ if isinstance(start, datetime) else occ.date())
    return out


def _normalize_until(body: str, dtstart: datetime) -> str:
    """dateutil insists UNTIL is UTC exactly when DTSTART is time-zone aware.

    Real calendars contain every combination (date-only, floating, UTC)."""
    m = re.search(r"UNTIL=([0-9TZ]+)", body, re.I)
    if not m:
        return body
    value = m.group(1).upper()
    aware = dtstart.tzinfo is not None
    if "T" not in value:   # date only: the whole last day counts
        new = value[:8] + "T235959" + ("Z" if aware else "")
    elif aware and not value.endswith("Z"):
        local = datetime.strptime(value[:15], "%Y%m%dT%H%M%S").replace(tzinfo=dtstart.tzinfo)
        new = local.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    elif not aware and value.endswith("Z"):
        new = value[:-1]
    else:
        return body
    return body[:m.start(1)] + new + body[m.end(1):]


# ------------------------------------------------------------------ zones

# Common Windows zone IDs (as used by Exchange) -> IANA, from CLDR windowsZones.
WINDOWS_TO_IANA = {
    "Dateline Standard Time": "Etc/GMT+12", "UTC-11": "Etc/GMT+11",
    "Hawaiian Standard Time": "Pacific/Honolulu", "Alaskan Standard Time": "America/Anchorage",
    "Pacific Standard Time": "America/Los_Angeles",
    "Pacific Standard Time (Mexico)": "America/Tijuana",
    "US Mountain Standard Time": "America/Phoenix", "Mountain Standard Time": "America/Denver",
    "Mountain Standard Time (Mexico)": "America/Mazatlan",
    "Central America Standard Time": "America/Guatemala",
    "Central Standard Time": "America/Chicago",
    "Central Standard Time (Mexico)": "America/Mexico_City",
    "Canada Central Standard Time": "America/Regina",
    "SA Pacific Standard Time": "America/Bogota", "Eastern Standard Time": "America/New_York",
    "Eastern Standard Time (Mexico)": "America/Cancun",
    "US Eastern Standard Time": "America/Indiana/Indianapolis",
    "Venezuela Standard Time": "America/Caracas", "Atlantic Standard Time": "America/Halifax",
    "SA Western Standard Time": "America/La_Paz",
    "Central Brazilian Standard Time": "America/Cuiaba",
    "Pacific SA Standard Time": "America/Santiago",
    "Newfoundland Standard Time": "America/St_Johns",
    "E. South America Standard Time": "America/Sao_Paulo",
    "Argentina Standard Time": "America/Argentina/Buenos_Aires",
    "SA Eastern Standard Time": "America/Cayenne", "Greenland Standard Time": "America/Nuuk",
    "Montevideo Standard Time": "America/Montevideo", "UTC-02": "Etc/GMT+2",
    "Azores Standard Time": "Atlantic/Azores", "Cape Verde Standard Time": "Atlantic/Cape_Verde",
    "UTC": "Etc/UTC", "Coordinated Universal Time": "Etc/UTC",
    "GMT Standard Time": "Europe/London",
    "Greenwich Standard Time": "Atlantic/Reykjavik", "Morocco Standard Time": "Africa/Casablanca",
    "W. Europe Standard Time": "Europe/Berlin",
    "Central Europe Standard Time": "Europe/Budapest",
    "Romance Standard Time": "Europe/Paris", "Central European Standard Time": "Europe/Warsaw",
    "W. Central Africa Standard Time": "Africa/Lagos", "GTB Standard Time": "Europe/Bucharest",
    "Middle East Standard Time": "Asia/Beirut", "Egypt Standard Time": "Africa/Cairo",
    "E. Europe Standard Time": "Europe/Chisinau", "South Africa Standard Time": "Africa/Johannesburg",
    "FLE Standard Time": "Europe/Kiev", "Israel Standard Time": "Asia/Jerusalem",
    "Kaliningrad Standard Time": "Europe/Kaliningrad", "Jordan Standard Time": "Asia/Amman",
    "Arabic Standard Time": "Asia/Baghdad", "Turkey Standard Time": "Europe/Istanbul",
    "Arab Standard Time": "Asia/Riyadh", "Belarus Standard Time": "Europe/Minsk",
    "Russian Standard Time": "Europe/Moscow", "E. Africa Standard Time": "Africa/Nairobi",
    "Iran Standard Time": "Asia/Tehran", "Arabian Standard Time": "Asia/Dubai",
    "Azerbaijan Standard Time": "Asia/Baku", "Georgian Standard Time": "Asia/Tbilisi",
    "Caucasus Standard Time": "Asia/Yerevan", "Afghanistan Standard Time": "Asia/Kabul",
    "West Asia Standard Time": "Asia/Tashkent", "Pakistan Standard Time": "Asia/Karachi",
    "India Standard Time": "Asia/Kolkata", "Sri Lanka Standard Time": "Asia/Colombo",
    "Nepal Standard Time": "Asia/Kathmandu", "Central Asia Standard Time": "Asia/Almaty",
    "Bangladesh Standard Time": "Asia/Dhaka", "Myanmar Standard Time": "Asia/Yangon",
    "SE Asia Standard Time": "Asia/Bangkok", "N. Central Asia Standard Time": "Asia/Novosibirsk",
    "China Standard Time": "Asia/Shanghai", "North Asia East Standard Time": "Asia/Irkutsk",
    "Singapore Standard Time": "Asia/Singapore", "W. Australia Standard Time": "Australia/Perth",
    "Taipei Standard Time": "Asia/Taipei", "Ulaanbaatar Standard Time": "Asia/Ulaanbaatar",
    "Tokyo Standard Time": "Asia/Tokyo", "Korea Standard Time": "Asia/Seoul",
    "Cen. Australia Standard Time": "Australia/Adelaide",
    "AUS Central Standard Time": "Australia/Darwin",
    "E. Australia Standard Time": "Australia/Brisbane",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "West Pacific Standard Time": "Pacific/Port_Moresby",
    "Tasmania Standard Time": "Australia/Hobart", "Vladivostok Standard Time": "Asia/Vladivostok",
    "Central Pacific Standard Time": "Pacific/Guadalcanal",
    "New Zealand Standard Time": "Pacific/Auckland", "UTC+12": "Etc/GMT-12",
    "Fiji Standard Time": "Pacific/Fiji", "Tonga Standard Time": "Pacific/Tongatapu",
    "Samoa Standard Time": "Pacific/Apia", "Line Islands Standard Time": "Pacific/Kiritimati",
}
IANA_TO_WINDOWS = {}
for _win, _iana in WINDOWS_TO_IANA.items():
    IANA_TO_WINDOWS.setdefault(_iana, _win)


def to_iana(name: str) -> str:
    if not name:
        return "UTC"
    if name in WINDOWS_TO_IANA:
        return WINDOWS_TO_IANA[name]
    try:
        ZoneInfo(name)
        return name
    except (ZoneInfoNotFoundError, ValueError):
        return "UTC"


def zone(name: str):
    try:
        return ZoneInfo(to_iana(name))
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def local_parts(dt: datetime, tz: str) -> str:
    """'YYYY-MM-DDTHH:MM:SS' wall-clock time of an aware datetime in ``tz``."""
    return dt.astimezone(zone(tz)).replace(tzinfo=None).isoformat(timespec="seconds")


def parse_local(value: str, tz: str) -> datetime:
    """Parse Graph/Google 'dateTime' (naive wall clock in ``tz``) into aware UTC."""
    value = value.rstrip("Z")
    if "." in value:
        value = value.split(".")[0]
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=zone(tz))
    return dt.astimezone(timezone.utc)


def day_after(d: date) -> date:
    return d + timedelta(days=1)
