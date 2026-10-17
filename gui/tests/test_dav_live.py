"""Reads from real CardDAV/CalDAV (Radicale) and WebDAV (WsgiDAV) servers running locally."""

import threading

import pytest
import requests
import responses

from mailmigrator.cloud.dav import CalDavCalendar, CardDavContacts, WebDavFiles
from mailmigrator.cloud.google import GoogleCalendar, GoogleContacts
from mailmigrator.cloud.graph import GraphDrive
from mailmigrator.cloud.migrate import (CloudOptions, migrate_calendars, migrate_contacts,
                                        migrate_files)
from mailmigrator.cloud.state import State

from .fakes import FakeGoogle, FakeGraph
from .test_cloud import client

radicale = pytest.importorskip("radicale")
cheroot_wsgi = pytest.importorskip("cheroot.wsgi")
wsgidav_app = pytest.importorskip("wsgidav.wsgidav_app")

VCARDS = [
    "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:u1\r\nFN:Ana Ruiz\r\nN:Ruiz;Ana;;;\r\n"
    "EMAIL;TYPE=WORK:ana@old.org\r\nTEL;TYPE=CELL:+34 600\r\nCATEGORIES:Team\r\nEND:VCARD\r\n",
    "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:u2\r\nFN:Bo Chen\r\nN:Chen;Bo;;;\r\n"
    "EMAIL:bo@old.org\r\nEND:VCARD\r\n",
]
ICS = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\nBEGIN:VEVENT\r\nUID:ev1@test\r\n"
       "DTSTAMP:20250101T000000Z\r\nDTSTART:20300301T150000Z\r\nDTEND:20300301T160000Z\r\n"
       "SUMMARY:Planning\r\nRRULE:FREQ=WEEKLY;COUNT=3\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


def serve(app):
    server = cheroot_wsgi.Server(("127.0.0.1", 0), app)
    server.prepare()
    thread = threading.Thread(target=server.serve, daemon=True)
    thread.start()
    host, port = server.bind_addr
    return server, f"http://{host}:{port}"


@pytest.fixture
def radicale_url(tmp_path):
    from radicale import Application, config
    conf = config.load()
    conf.update({"auth": {"type": "none"}, "rights": {"type": "owner_only"},
                 "storage": {"filesystem_folder": str(tmp_path / "radicale")}}, "test")
    server, url = serve(Application(conf))
    s = requests.Session()
    s.auth = ("alice", "pw")
    book = f"{url}/alice/contacts/"
    cal = f"{url}/alice/work/"
    s.request("MKCOL", book, data=(
        '<?xml version="1.0"?><mkcol xmlns="DAV:" xmlns:C="urn:ietf:params:xml:ns:carddav">'
        '<set><prop><resourcetype><collection/><C:addressbook/></resourcetype>'
        '<displayname>Contacts</displayname></prop></set></mkcol>')).raise_for_status()
    s.request("MKCALENDAR", cal, data=(
        '<?xml version="1.0"?><C:mkcalendar xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
        '<D:set><D:prop><D:displayname>Work</D:displayname></D:prop></D:set></C:mkcalendar>')
    ).raise_for_status()
    for i, card in enumerate(VCARDS):
        s.put(f"{book}c{i}.vcf", data=card.encode(),
              headers={"Content-Type": "text/vcard"}).raise_for_status()
    s.put(f"{cal}e1.ics", data=ICS.encode(),
          headers={"Content-Type": "text/calendar"}).raise_for_status()
    yield url
    server.stop()


@pytest.fixture
def webdav_url(tmp_path):
    root = tmp_path / "files"
    (root / "Projects" / "2025").mkdir(parents=True)
    (root / "Projects" / "2025" / "notes.txt").write_bytes(b"meeting notes")
    (root / "photo.jpg").write_bytes(b"\xff\xd8" + b"x" * 5000)
    app = wsgidav_app.WsgiDAVApp({"provider_mapping": {"/": str(root)},
                                  "simple_dc": {"user_mapping": {"*": True}},
                                  "verbose": 0, "logging": {"enable": False}})
    server, url = serve(app)
    yield url
    server.stop()


@pytest.fixture
def rsps():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as r:
        r.add_passthru("http://127.0.0.1")
        yield r


def test_carddav_and_caldav_discovery_into_google(radicale_url, rsps, tmp_path):
    g = FakeGoogle(rsps)
    st = State(str(tmp_path / "st"), "dav", "google")
    # Only the server root is given; principal and home sets are discovered.
    contacts = CardDavContacts(radicale_url, "alice", "pw")
    stats = migrate_contacts(contacts, GoogleContacts(client()), st, CloudOptions())
    assert stats.created == 2 and stats.failed == 0
    emails = sorted(p["emailAddresses"][0]["value"] for p in g.people.values())
    assert emails == ["ana@old.org", "bo@old.org"]
    assert any(grp.get("name") == "Team" for grp in g.groups.values())

    cal = CalDavCalendar(radicale_url, "alice", "pw")
    assert [c.name for c in cal.owned_calendars()] == ["Work"]
    stats = migrate_calendars(cal, GoogleCalendar(client()), st, CloudOptions(), True)
    assert stats.created == 1 and stats.failed == 0
    # The first DAV calendar is treated as the main one and lands in the primary calendar.
    planning = [e for e in g.calendars["primary@x"]["events"].values()
                if e.get("summary") == "Planning"]
    assert len(planning) == 1
    assert planning[0]["recurrence"] == ["RRULE:FREQ=WEEKLY;COUNT=3"]
    assert planning[0]["start"]["dateTime"] == "2030-03-01T15:00:00Z"
    st.close()


def test_webdav_files_into_onedrive(webdav_url, rsps, tmp_path):
    ms = FakeGraph(rsps)
    st = State(str(tmp_path / "st"), "webdav", "onedrive")
    stats = migrate_files(WebDavFiles(webdav_url, "u", "p"), GraphDrive(client(), "d1"), st,
                          CloudOptions(temp_dir=str(tmp_path), copy_permissions=False))
    assert stats.failed == 0, stats.errors
    assert stats.created == 2
    paths = sorted(ms.path_of(i) for i, it in ms.items.items() if "file" in it)
    assert paths == ["Projects/2025/notes.txt", "photo.jpg"]
    notes = [it for it in ms.items.values() if it["name"] == "notes.txt"][0]
    assert notes["content"] == b"meeting notes"
    st.close()
