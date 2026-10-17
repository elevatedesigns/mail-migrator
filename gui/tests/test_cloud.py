import base64
import json
import threading
import time
import urllib.request
from datetime import date, datetime, timezone

import pytest
import responses

from mailmigrator.cloud import auth
from mailmigrator.cloud.dav import parse_ics, parse_vcards
from mailmigrator.cloud.google import (GoogleCalendar, GoogleContacts, GoogleDrive,
                                       GoogleSharedDrives)
from mailmigrator.cloud.graph import (GraphCalendar, GraphContacts, GraphDrive, contact_to_graph,
                                      graph_to_contact, safe_name)
from mailmigrator.cloud.http import ApiError, Client
from mailmigrator.cloud.migrate import (CloudOptions, DrivePlan, migrate_calendars,
                                        migrate_contacts, migrate_files, migrate_shared_drive)
from mailmigrator.cloud.models import Contact, Drive
from mailmigrator.cloud.recurrence import (Unsupported, graph_to_rrule, rrule_to_graph,
                                           to_iana)
from mailmigrator.cloud.state import State, UserMap

from .fakes import FakeGoogle, FakeGraph, Tok


@pytest.fixture
def rsps():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as r:
        r.add_passthru("http://127.0.0.1")
        r.add_passthru("http://localhost")
        yield r


@pytest.fixture
def state(tmp_path):
    s = State(str(tmp_path / "state"), "a", "b")
    yield s
    s.close()


def client():
    c = Client(Tok())
    c._sleep = lambda s: None
    return c


# ------------------------------------------------------------ conversions

@pytest.mark.parametrize("rule,start,kind", [
    ("RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=10", date(2025, 1, 6), "weekly"),
    ("RRULE:FREQ=DAILY;INTERVAL=2;UNTIL=20250301T000000Z", date(2025, 1, 1), "daily"),
    ("RRULE:FREQ=MONTHLY;BYDAY=2TU", date(2025, 1, 14), "relativeMonthly"),
    ("RRULE:FREQ=MONTHLY;BYDAY=FR;BYSETPOS=-1", date(2025, 1, 31), "relativeMonthly"),
    ("RRULE:FREQ=MONTHLY;BYMONTHDAY=15", date(2025, 1, 15), "absoluteMonthly"),
    ("RRULE:FREQ=YEARLY;BYMONTH=3;BYMONTHDAY=8", date(2025, 3, 8), "absoluteYearly"),
    ("RRULE:FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR", date(2025, 1, 6), "weekly"),
])
def test_rrule_round_trip(rule, start, kind):
    from dateutil.rrule import rrulestr
    g = rrule_to_graph(rule, start, "Eastern Standard Time")
    assert g["pattern"]["type"] == kind
    back = graph_to_rrule(g)
    dt = datetime(start.year, start.month, start.day, 9, tzinfo=timezone.utc)
    a = list(rrulestr(rule.split(":", 1)[1], dtstart=dt)[:12]) if "UNTIL" not in rule else \
        list(rrulestr(rule.split(":", 1)[1], dtstart=dt))
    b = list(rrulestr(back.split(":", 1)[1], dtstart=dt)[:12]) if "UNTIL" not in back else \
        list(rrulestr(back.split(":", 1)[1], dtstart=dt))
    assert [x.date() for x in a[:12]] == [x.date() for x in b[:12]]


def test_expand_handles_mixed_until_forms():
    from mailmigrator.cloud.recurrence import expand
    aware = datetime(2025, 1, 6, 9, tzinfo=timezone.utc)
    for until in ("20250120", "20250120T090000", "20250120T090000Z"):
        got = expand(f"RRULE:FREQ=WEEKLY;UNTIL={until}", aware, datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert len(got) == 3, until
    naive = expand("RRULE:FREQ=DAILY;UNTIL=20250103T000000Z", date(2025, 1, 1), date(2026, 1, 1))
    assert naive == [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)]


def test_rrule_unsupported():
    with pytest.raises(Unsupported):
        rrule_to_graph("RRULE:FREQ=MONTHLY;BYDAY=MO", date(2025, 1, 6), "UTC")
    with pytest.raises(Unsupported):
        rrule_to_graph("RRULE:FREQ=HOURLY", date(2025, 1, 6), "UTC")


def test_zones():
    assert to_iana("Pacific Standard Time") == "America/Los_Angeles"
    assert to_iana("Europe/Paris") == "Europe/Paris"
    assert to_iana("Nowhere/Fake") == "UTC"


def test_vcard_parsing():
    text = ("BEGIN:VCARD\r\nVERSION:3.0\r\nN:Doe;Jane;Q.;Dr.;PhD\r\nFN:Dr. Jane Doe\r\n"
            "EMAIL;TYPE=INTERNET;TYPE=WORK:jane@work.com\r\nitem1.EMAIL:jane@home.com\r\n"
            "item1.X-ABLabel:Personal\r\nTEL;TYPE=CELL:+1 555 0100\r\nTEL;WORK:+1 555 0101\r\n"
            "ADR;TYPE=HOME:;;1 Main St;Springfield;IL;62701;USA\r\nORG:Acme;R&D\r\n"
            "TITLE:Engineer\r\nBDAY:--0315\r\nNOTE:Line one\\nLine two\\, with comma\r\n"
            "CATEGORIES:Friends,Work\r\nURL:https://jane.example\r\nEND:VCARD\r\n"
            "BEGIN:VCARD\r\nVERSION:2.1\r\nN:Smith;Bob\r\nTEL;HOME;VOICE:555\r\n"
            "BDAY:1990-07-04\r\nNOTE:folded \r\n line\r\nEND:VCARD\r\n")
    jane, bob = parse_vcards(text)
    assert (jane.given_name, jane.family_name, jane.prefix, jane.suffix) == \
        ("Jane", "Doe", "Dr.", "PhD")
    assert jane.emails == [("work", "jane@work.com"), ("other", "jane@home.com")]
    assert ("mobile", "+1 555 0100") in jane.phones and ("work", "+1 555 0101") in jane.phones
    assert jane.addresses[0].city == "Springfield" and jane.company == "Acme"
    assert jane.department == "R&D" and jane.birthday == "--03-15"
    assert jane.notes == "Line one\nLine two, with comma"
    assert jane.groups == ["Friends", "Work"]
    assert bob.phones == [("home", "555")] and bob.birthday == "1990-07-04"
    assert bob.notes == "folded line"


def test_ics_parsing():
    ics = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:abc@x
DTSTART;TZID=America/New_York:20250106T090000
DTEND;TZID=America/New_York:20250106T100000
RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=5
EXDATE;TZID=America/New_York:20250113T090000
SUMMARY:Standup
ORGANIZER:mailto:boss@old.com
ATTENDEE;CN=Ann;PARTSTAT=ACCEPTED:mailto:ann@old.com
BEGIN:VALARM
TRIGGER:-PT15M
ACTION:DISPLAY
END:VALARM
END:VEVENT
BEGIN:VEVENT
UID:abc@x
RECURRENCE-ID;TZID=America/New_York:20250120T090000
DTSTART;TZID=America/New_York:20250120T110000
DTEND;TZID=America/New_York:20250120T120000
SUMMARY:Standup (moved)
END:VEVENT
BEGIN:VEVENT
UID:allday@x
DTSTART;VALUE=DATE:20250704
DTEND;VALUE=DATE:20250705
SUMMARY:Holiday
END:VEVENT
END:VCALENDAR
"""
    master, moved, holiday = parse_ics(ics)
    assert master.timezone == "America/New_York"
    assert master.start == datetime(2025, 1, 6, 14, tzinfo=timezone.utc)
    assert master.recurrence[0].startswith("RRULE:FREQ=WEEKLY")
    assert master.recurrence[1] == "EXDATE:20250113T140000Z"
    assert master.reminders == [15] and master.attendees[0].response == "accepted"
    assert moved.is_exception and moved.original_start == datetime(2025, 1, 20, 14,
                                                                    tzinfo=timezone.utc)
    assert holiday.all_day and holiday.start == date(2025, 7, 4)


def test_user_map():
    m = UserMap("alice@old.com = alice.smith@new.com\n@old.com = @new.com\n# c\nbad line\n")
    assert m.map("Alice@old.com") == "alice.smith@new.com"
    assert m.map("bob@old.com") == "bob@new.com"
    assert m.map("x@elsewhere.com") == "x@elsewhere.com"
    assert m.problems == ["Line 4: expected 'old = new'"]


def test_contact_to_graph_overflow():
    c = Contact(display_name="Many", emails=[("work", f"m{i}@x.com") for i in range(5)],
                phones=[("other", "1"), ("work", "2")], birthday="--12-25")
    g = contact_to_graph(c)
    assert len(g["emailAddresses"]) == 3 and "Email: m4@x.com" in g["personalNotes"]
    assert g["birthday"] == "1604-12-25T11:59:00Z"
    assert graph_to_contact({**g, "id": "1"}).birthday == "--12-25"


def test_safe_names():
    assert safe_name('Q1: "plan" <draft>?.') == "Q1_ _plan_ _draft__"
    assert safe_name("con") == "_con"


# ------------------------------------------------------------------- auth

def test_service_account_jwt(rsps):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    seen = {}

    def token(req):
        form = dict(x.split("=", 1) for x in req.body.split("&"))
        seen["assertion"] = urllib.request.unquote(form["assertion"])
        return 200, {}, json.dumps({"access_token": "sa-token", "expires_in": 3600})

    rsps.add_callback("POST", auth.GOOGLE_TOKEN, token)
    sa = auth.GoogleServiceAccount({"client_email": "svc@proj.iam.gserviceaccount.com",
                                    "private_key": pem}, "admin@corp.com")
    user = sa.for_user("bob@corp.com")
    assert user.token("google") == "sa-token"
    head, claims, sig = seen["assertion"].split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)
    body = json.loads(base64.urlsafe_b64decode(pad(claims)))
    assert body["sub"] == "bob@corp.com" and "https://mail.google.com/" in body["scope"]
    key.public_key().verify(base64.urlsafe_b64decode(pad(sig)), f"{head}.{claims}".encode(),
                            padding.PKCS1v15(), hashes.SHA256())
    assert user.token("google") == "sa-token" and len(rsps.calls) == 1  # cached


def test_browser_sign_in_microsoft(rsps):
    id_token = "x." + base64.urlsafe_b64encode(json.dumps(
        {"preferred_username": "pat@contoso.com", "name": "Pat"}).encode()).decode().rstrip("=") + ".y"
    seen = {}

    def token(req):
        form = dict(x.split("=", 1) for x in req.body.split("&"))
        seen.setdefault("grants", []).append(form["grant_type"])
        seen.setdefault("scopes", []).append(urllib.request.unquote(form.get("scope", "")))
        return 200, {}, json.dumps({"access_token": "ms-" + form["grant_type"],
                                    "refresh_token": "rt2", "expires_in": 3600,
                                    "id_token": id_token})

    rsps.add_callback("POST", f"{auth.MS_LOGIN}/common/oauth2/v2.0/token", token)

    def fake_browser(url):
        q = dict(x.split("=", 1) for x in url.split("?", 1)[1].split("&"))
        redirect = urllib.request.unquote(q["redirect_uri"]).replace("localhost", "127.0.0.1")
        assert q["code_challenge_method"] == "S256"
        assert "IMAP.AccessAsUser.All" in urllib.request.unquote(q["scope"])

        def hit():
            time.sleep(0.2)
            urllib.request.urlopen(f"{redirect}?code=abc&state={q['state']}").read()
        threading.Thread(target=hit, daemon=True).start()

    cred = auth.MicrosoftUser.sign_in("client-1", open_url=fake_browser, timeout=20)
    assert cred.email == "pat@contoso.com" and cred.token("graph") == "ms-authorization_code"
    assert cred.token("imap") == "ms-refresh_token"
    assert "outlook.office.com/IMAP.AccessAsUser.All" in seen["scopes"][-1]


def test_sign_in_state_mismatch_rejected(rsps):
    def fake_browser(url):
        q = dict(x.split("=", 1) for x in url.split("?", 1)[1].split("&"))
        redirect = urllib.request.unquote(q["redirect_uri"])
        threading.Thread(target=lambda: (time.sleep(0.2), urllib.request.urlopen(
            f"{redirect}?code=abc&state=forged").read()), daemon=True).start()

    with pytest.raises(auth.AuthError, match="did not match"):
        auth.GoogleUser.sign_in("cid", "secret", open_url=fake_browser, timeout=20)


def test_http_retries_throttling(rsps):
    rsps.add("GET", "https://api.test/x", status=429, headers={"Retry-After": "1"})
    rsps.add("GET", "https://api.test/x", json={"ok": True})
    assert client().json("GET", "https://api.test/x") == {"ok": True}
    rsps.add("GET", "https://api.test/y", status=403,
             json={"error": {"code": 403, "message": "Insufficient permission"}})
    with pytest.raises(ApiError, match="Insufficient permission"):
        client().json("GET", "https://api.test/y")


def test_google_paging(rsps):
    rsps.add("GET", "https://api.test/list", json={"items": [1, 2], "nextPageToken": "p2"})
    rsps.add("GET", "https://api.test/list", json={"items": [3]})
    assert list(client().pages("https://api.test/list", "items")) == [1, 2, 3]
    assert "pageToken=p2" in rsps.calls[1].request.url


# --------------------------------------------------------------- contacts

def test_contacts_google_to_microsoft_and_back(rsps, state):
    g, ms = FakeGoogle(rsps), FakeGraph(rsps)
    g.groups["contactGroups/9"] = {"resourceName": "contactGroups/9", "name": "Family",
                                   "groupType": "USER_CONTACT_GROUP"}
    g.people["people/1"] = {
        "resourceName": "people/1",
        "names": [{"givenName": "Ann", "familyName": "Lee", "displayName": "Ann Lee"}],
        "emailAddresses": [{"value": f"ann{i}@x.com", "type": "home"} for i in range(4)],
        "phoneNumbers": [{"value": "555", "type": "mobile"}],
        "birthdays": [{"date": {"month": 2, "day": 29}}],
        "memberships": [{"contactGroupMembership": {"contactGroupResourceName": "contactGroups/9"}}]}
    src, dst = GoogleContacts(client()), GraphContacts(client(), "me@contoso.com", True)
    opts = CloudOptions()
    stats = migrate_contacts(src, dst, state, opts)
    assert stats.created == 1 and stats.failed == 0
    made = next(iter(ms.contacts.values()))
    assert made["categories"] == ["Family"] and made["mobilePhone"] == "555"
    assert made["birthday"] == "1604-02-29T11:59:00Z"
    assert "ann3@x.com" in made["personalNotes"]
    # Re-run: unchanged contacts are skipped
    assert migrate_contacts(src, dst, state, opts).skipped == 1
    # Changed at the source: updated, not duplicated
    g.people["people/1"]["phoneNumbers"][0]["value"] = "556"
    stats = migrate_contacts(src, dst, state, opts)
    assert stats.updated == 1 and len(ms.contacts) == 1
    assert next(iter(ms.contacts.values()))["mobilePhone"] == "556"


def test_contacts_microsoft_to_google(rsps, tmp_path):
    g, ms = FakeGoogle(rsps), FakeGraph(rsps)
    ms.contacts["c1"] = {"id": "c1", "givenName": "Kim", "surname": "Ng",
                         "emailAddresses": [{"address": "kim@contoso.com"}],
                         "businessPhones": ["111"], "categories": ["Clients"]}
    ms.folders["f1"] = {"id": "f1", "displayName": "Vendors"}
    ms.contacts["c2"] = {"id": "c2", "displayName": "Supply Co", "_folder": "f1",
                         "companyName": "Supply Co"}
    st = State(str(tmp_path / "s"), "m", "g")
    stats = migrate_contacts(GraphContacts(client(), "", True), GoogleContacts(client()), st,
                             CloudOptions())
    assert stats.created == 2
    names = {g.groups[k]["name"] for k in g.groups if k != "contactGroups/myContacts"}
    assert names == {"Clients", "Vendors"}
    kim = [p for p in g.people.values() if p.get("names", [{}])[0].get("givenName") == "Kim"][0]
    assert kim["phoneNumbers"] == [{"value": "111", "type": "work"}]
    st.close()


# -------------------------------------------------------------- calendars

def g_event(eid, uid, start, end, **kw):
    return {"id": eid, "iCalUID": uid, "status": "confirmed", "summary": kw.pop("summary", eid),
            "start": {"dateTime": start, "timeZone": "America/New_York"},
            "end": {"dateTime": end, "timeZone": "America/New_York"}, **kw}


def test_calendar_google_to_microsoft(rsps, state):
    g, ms = FakeGoogle(rsps), FakeGraph(rsps)
    ev = g.calendars["primary@x"]["events"]
    ev["s1"] = g_event("s1", "single@x", "2030-03-02T10:00:00-05:00", "2030-03-02T11:00:00-05:00",
                       attendees=[{"email": "ann@old.com", "responseStatus": "accepted"}],
                       organizer={"email": "me@old.com"})
    ev["r1"] = g_event("r1", "series@x", "2030-01-07T09:00:00-05:00", "2030-01-07T09:30:00-05:00",
                       summary="Standup",
                       recurrence=["RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=6",
                                   "EXDATE;TZID=America/New_York:20300114T090000"])
    ev["r1_moved"] = g_event("r1_moved", "series@x", "2030-01-21T11:00:00-05:00",
                             "2030-01-21T11:30:00-05:00", summary="Standup (moved)",
                             recurringEventId="r1",
                             originalStartTime={"dateTime": "2030-01-21T09:00:00-05:00",
                                                "timeZone": "America/New_York"})
    ev["r1_gone"] = {"id": "r1_gone", "iCalUID": "series@x", "status": "cancelled",
                     "recurringEventId": "r1",
                     "originalStartTime": {"dateTime": "2030-01-28T09:00:00-05:00"}}
    ev["wl"] = {**g_event("wl", "wl@x", "2030-01-06T09:00:00Z", "2030-01-06T17:00:00Z"),
                "eventType": "workingLocation"}
    opts = CloudOptions(user_map=UserMap("@old.com = @new.com"))
    stats = migrate_calendars(GoogleCalendar(client()), GraphCalendar(client(), "", True),
                              state, opts, dest_is_google=False)
    assert stats.failed == 0, stats.errors
    assert stats.created == 4
    assert ms.created_with_attendees == 0          # no invitations sent
    events = ms.calendars["calA"]["events"]
    single = [e for e in events.values() if e["subject"] == "s1"][0]
    assert "ann@new.com" in single["body"]["content"]          # attendees noted, mapped
    master = [e for e in events.values() if e["subject"] == "Standup"][0]
    assert master["recurrence"]["pattern"]["daysOfWeek"] == ["monday"]
    assert master["start"] == {"dateTime": "2030-01-07T09:00:00",
                               "timeZone": "Eastern Standard Time"}
    deleted = {i.split("_")[1] for i in ms.deleted_instances}
    assert deleted == {"20300114T1400", "20300128T1400"}         # EXDATE + cancelled
    patched = [v for v in ms.patched_instances.values()]
    assert patched[0]["subject"] == "Standup (moved)"
    # Re-run copies nothing new
    again = migrate_calendars(GoogleCalendar(client()), GraphCalendar(client(), "", True),
                              state, opts, dest_is_google=False)
    assert again.created == 0 and again.skipped == 4


def test_calendar_microsoft_to_google(rsps, state):
    g, ms = FakeGoogle(rsps), FakeGraph(rsps)
    gcal = GraphCalendar(client(), "", True)
    from mailmigrator.cloud.models import Event
    master_id = gcal.import_event("calA", Event(
        source_id="m", uid="u", summary="Weekly", timezone="Europe/Paris",
        start=datetime(2030, 2, 4, 9, tzinfo=timezone.utc),
        end=datetime(2030, 2, 4, 10, tzinfo=timezone.utc),
        recurrence=["RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=4"]))
    ms.deleted_instances.add(f"{master_id}_20300211T0900")
    ms.patched_instances[f"{master_id}_20300218T0900"] = {"subject": "Weekly (longer)"}
    ms.calendars["calA"]["events"][master_id]["attendees"] = [
        {"emailAddress": {"address": "x@contoso.com", "name": "X"}, "type": "required",
         "status": {"response": "accepted"}}]
    stats = migrate_calendars(gcal, GoogleCalendar(client()), state, CloudOptions(),
                              dest_is_google=True)
    assert stats.failed == 0, stats.errors
    imported = list(g.calendars["primary@x"]["events"].values())
    master = [e for e in imported if e.get("recurrence")][0]
    assert "EXDATE:20300211T090000Z" in master["recurrence"]
    assert master["attendees"][0]["email"] == "x@contoso.com"   # import never notifies
    exc = [e for e in imported if e.get("originalStartTime")][0]
    assert exc["summary"] == "Weekly (longer)" and exc["recurringEventId"] == master["id"]


# ------------------------------------------------------------------ files

def test_files_google_to_onedrive(rsps, state, tmp_path):
    g, ms = FakeGoogle(rsps), FakeGraph(rsps)
    docs = g.add_folder("Docs")
    g.add_file("plan.txt", docs, b"hello")
    g.add_file("plan.txt", docs, b"second file, same name")        # Drive allows duplicates
    g.add_file("Q1: budget?", docs, b"x", "application/vnd.google-apps.spreadsheet")
    g.add_file("Survey", docs, b"", "application/vnd.google-apps.form")
    big = g.add_file("video.bin", "root", b"v" * (5 * 1024 * 1024 + 123))
    g.perms[big] = [{"type": "user", "role": "writer", "emailAddress": "bob@old.com"},
                    {"type": "user", "role": "owner", "emailAddress": "me@old.com"},
                    {"type": "user", "role": "reader", "emailAddress": "inh@old.com",
                     "permissionDetails": [{"inherited": True}]}]
    opts = CloudOptions(user_map=UserMap("@old.com = @new.com"), temp_dir=str(tmp_path),
                        files_folder="From Google Drive")
    stats = migrate_files(GoogleDrive(client()), GraphDrive(client(), "d1"), state, opts)
    assert stats.failed == 0, stats.errors
    paths = {ms.path_of(i): it for i, it in ms.items.items() if "file" in it}
    assert set(paths) == {"From Google Drive/Docs/plan.txt",
                          "From Google Drive/Docs/plan (2).txt",
                          "From Google Drive/Docs/Q1_ budget_.xlsx",
                          "From Google Drive/video.bin"}
    assert paths["From Google Drive/Docs/Q1_ budget_.xlsx"]["content"].startswith(b"EXPORTED")
    video = [i for i, it in ms.items.items() if it["name"] == "video.bin"][0]
    assert ms.items[video]["size"] == 5 * 1024 * 1024 + 123
    assert ms.items[video]["fileSystemInfo"]["lastModifiedDateTime"] == "2024-01-02T03:04:05Z"
    assert ms.perms[video] == [{"id": ms.perms[video][0]["id"], "roles": ["write"],
                                "grantedToV2": {"user": {"email": "bob@new.com"}}}]
    assert stats.skipped == 1  # the Google Form
    again = migrate_files(GoogleDrive(client()), GraphDrive(client(), "d1"), state, opts)
    assert again.created == 0 and again.skipped == 5


def test_files_onedrive_to_google_with_conversion(rsps, state, tmp_path):
    g, ms = FakeGoogle(rsps), FakeGraph(rsps)
    folder = ms.add_item("Reports", folder=True)
    ms.add_item("summary.docx", folder, b"docx-bytes")
    big = ms.add_item("data.csv", "root", b"c" * (6 * 1024 * 1024))
    ms.perms[big] = [{"id": "p", "roles": ["read"], "link": {"scope": "organization",
                                                             "type": "view"}}]
    opts = CloudOptions(convert_documents=True, temp_dir=str(tmp_path),
                        user_map=UserMap("@contoso.com = @fabrikam.com"))
    stats = migrate_files(GraphDrive(client(), "d1"), GoogleDrive(client()), state, opts)
    assert stats.failed == 0, stats.errors
    by_name = {f["name"]: f for f in g.files.values()}
    assert by_name["summary"]["mimeType"] == "application/vnd.google-apps.document"
    assert by_name["data.csv"]["content"] == b"c" * (6 * 1024 * 1024)
    assert g.perms[by_name["data.csv"]["id"]][0] == {
        "type": "domain", "role": "reader", "domain": "fabrikam.com",
        "id": g.perms[by_name["data.csv"]["id"]][0]["id"]}


def test_dry_run_changes_nothing(rsps, state, tmp_path):
    g, ms = FakeGoogle(rsps), FakeGraph(rsps)
    g.add_file("a.txt", "root", b"1234")
    stats = migrate_files(GoogleDrive(client()), GraphDrive(client(), "d1"), state,
                          CloudOptions(dry_run=True, temp_dir=str(tmp_path)))
    assert stats.created == 1 and stats.bytes == 4
    assert not [i for i in ms.items.values() if "file" in i]


# ------------------------------------------------------------ shared drives

def test_shared_drive_google_to_google_creates_drive(rsps, state, tmp_path):
    g = FakeGoogle(rsps)
    src_drive = "srcdrive"
    g.drives[src_drive] = {"id": src_drive, "name": "Marketing"}
    g.files[src_drive] = {"id": src_drive, "name": "Marketing",
                          "mimeType": "application/vnd.google-apps.folder", "parents": []}
    g.add_file("brand.pdf", src_drive, b"pdf", "application/pdf", drive=src_drive)
    g.perms[src_drive] = [{"type": "user", "role": "organizer", "emailAddress": "lead@old.com"}]
    shared = GoogleSharedDrives(client(), admin=True)
    added = []
    stats = migrate_shared_drive(
        DrivePlan(Drive(src_drive, "Marketing")),
        open_source=lambda d: GoogleDrive(client(), d.id),
        open_dest=lambda d: GoogleDrive(client(), d.id),
        create_dest=lambda name: shared.create(name + " (migrated)"),
        members_of=lambda d: shared.members(d.id),
        add_member=lambda d, p: added.append((d.name, p.email, p.role)),
        state=state, opts=CloudOptions(user_map=UserMap("@old.com = @new.com"),
                                       temp_dir=str(tmp_path)))
    assert stats.created == 1 and stats.failed == 0
    new = [d for d in g.drives.values() if d["id"] != src_drive][0]
    assert new["name"] == "Marketing (migrated)"
    assert added == [("Marketing (migrated)", "lead@new.com", "owner")]
    assert [f["name"] for f in g.children(new["id"])] == ["brand.pdf"]
