"""In-memory stand-ins for the Google and Microsoft Graph APIs.

They implement only the endpoints and fields Mail Migrator uses, following the
public API documentation, so whole migrations can be exercised end to end
through the real HTTP client code (requests is intercepted by `responses`).
"""

from __future__ import annotations

import itertools
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlparse

import responses

_ids = itertools.count(1)


def nid(prefix: str) -> str:
    return f"{prefix}{next(_ids)}"


def _q(req) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(req.url).query).items()}


def _json(req) -> dict:
    body = req.body
    if not body:
        return {}
    if isinstance(body, bytes):
        body = body.decode()
    return json.loads(body)


def ok(data=None, status=200, headers=None):
    return (status, headers or {"Content-Type": "application/json"},
            json.dumps(data if data is not None else {}))


def err(status, message):
    return ok({"error": {"code": status, "message": message}}, status)


# =================================================================== Google

class FakeGoogle:
    def __init__(self, rsps: responses.RequestsMock) -> None:
        self.people: dict[str, dict] = {}
        self.groups: dict[str, dict] = {"contactGroups/myContacts": {
            "resourceName": "contactGroups/myContacts", "groupType": "SYSTEM_CONTACT_GROUP",
            "name": "myContacts"}}
        self.calendars: dict[str, dict] = {"primary@x": {"id": "primary@x", "summary": "Me",
                                                         "primary": True, "timeZone": "UTC",
                                                         "accessRole": "owner", "events": {}}}
        self.files: dict[str, dict] = {"root": {"id": "root", "name": "My Drive",
                                                "mimeType": "application/vnd.google-apps.folder",
                                                "parents": []}}
        self.perms: dict[str, list] = {}
        self.drives: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.import_calls = 0
        self._register(rsps)

    # -- helpers for tests --
    def add_file(self, name, parent="root", content=b"", mime="text/plain", drive=""):
        fid = nid("f")
        self.files[fid] = {"id": fid, "name": name, "mimeType": mime, "parents": [parent],
                           "size": str(len(content)), "content": content, "driveId": drive,
                           "modifiedTime": "2024-01-02T03:04:05Z"}
        return fid

    def add_folder(self, name, parent="root", drive=""):
        return self.add_file(name, parent, b"", "application/vnd.google-apps.folder", drive)

    def children(self, parent):
        return [f for f in self.files.values() if parent in f.get("parents", [])]

    def path_of(self, fid):
        parts, cur = [], self.files[fid]
        while cur.get("parents") and cur["parents"][0] in self.files and \
                cur["parents"][0] != "root" and cur["parents"][0] not in self.drives:
            cur = self.files[cur["parents"][0]]
            parts.insert(0, cur["name"])
        return "/".join(parts + [self.files[fid]["name"]])

    def _register(self, r: responses.RequestsMock) -> None:
        P = "https://people.googleapis.com/v1"
        C = "https://www.googleapis.com/calendar/v3"
        D = "https://www.googleapis.com/drive/v3"
        U = "https://www.googleapis.com/upload/drive/v3"
        cb = r.add_callback
        cb("GET", re.compile(re.escape(P) + r"/contactGroups\b.*"), self.list_groups)
        cb("POST", re.compile(re.escape(P) + r"/contactGroups$"), self.create_group)
        cb("GET", re.compile(re.escape(P) + r"/people/me/connections.*"), self.connections)
        cb("POST", re.compile(re.escape(P) + r"/people:batchCreateContacts.*"), self.batch_create)
        cb("PATCH", re.compile(re.escape(P) + r"/people/[^/:]+:updateContact.*"), self.update)
        cb("GET", re.compile(re.escape(P) + r"/people/[^/?]+\?.*"), self.get_person)
        cb("GET", re.compile(re.escape(C) + r"/users/me/calendarList.*"), self.cal_list)
        cb("POST", re.compile(re.escape(C) + r"/calendars$"), self.cal_create)
        cb("GET", re.compile(re.escape(C) + r"/calendars/[^/?]+/events.*"), self.events)
        cb("POST", re.compile(re.escape(C) + r"/calendars/[^/]+/events/import.*"), self.import_ev)
        cb("DELETE", re.compile(re.escape(C) + r"/calendars/[^/]+/events/[^/?]+.*"), self.del_ev)
        cb("GET", re.compile(re.escape(C) + r"/calendars/[^/?]+(\?.*)?$"), self.cal_get)
        cb("GET", re.compile(re.escape(D) + r"/files\?.*"), self.list_files)
        cb("POST", re.compile(re.escape(D) + r"/files\?.*"), self.create_folder)
        cb("GET", re.compile(re.escape(D) + r"/files/[^/?]+/permissions.*"), self.list_perms)
        cb("POST", re.compile(re.escape(D) + r"/files/[^/?]+/permissions.*"), self.add_perm)
        cb("GET", re.compile(re.escape(D) + r"/files/[^/?]+/export.*"), self.export)
        cb("GET", re.compile(re.escape(D) + r"/files/[^/?]+\?.*alt=media.*"), self.media)
        cb("POST", re.compile(re.escape(U) + r"/files\?.*"), self.upload)
        cb("PUT", re.compile(r"https://upload\.fake/google/.*"), self.upload_chunk)
        cb("GET", re.compile(re.escape(D) + r"/drives\?.*"), self.list_drives)
        cb("POST", re.compile(re.escape(D) + r"/drives\?.*"), self.create_drive)

    # -- people --
    def list_groups(self, req):
        return ok({"contactGroups": list(self.groups.values())})

    def create_group(self, req):
        name = _json(req)["contactGroup"]["name"]
        rn = nid("contactGroups/")
        self.groups[rn] = {"resourceName": rn, "name": name, "groupType": "USER_CONTACT_GROUP"}
        return ok(self.groups[rn])

    def connections(self, req):
        return ok({"connections": list(self.people.values())})

    def batch_create(self, req):
        out = []
        for entry in _json(req)["contacts"]:
            person = dict(entry["contactPerson"])
            rn = nid("people/c")
            person["resourceName"], person["etag"] = rn, "e1"
            self.people[rn] = person
            out.append({"person": {"resourceName": rn}})
        return ok({"createdPeople": out})

    def get_person(self, req):
        rn = unquote(urlparse(req.url).path.split("/v1/")[1])
        if rn not in self.people:
            return err(404, "not found")
        return ok(self.people[rn])

    def update(self, req):
        rn = unquote(urlparse(req.url).path.split("/v1/")[1].split(":")[0])
        if rn not in self.people:
            return err(404, "not found")
        body = _json(req)
        body.pop("etag", None)
        self.people[rn] = {**body, "resourceName": rn, "etag": "e2"}
        return ok(self.people[rn])

    # -- calendar --
    def cal_list(self, req):
        return ok({"items": [{k: v for k, v in c.items() if k != "events"}
                             for c in self.calendars.values()]})

    def _cal(self, req):
        cid = unquote(urlparse(req.url).path.split("/calendars/")[1].split("/")[0])
        return self.calendars["primary@x" if cid == "primary" else cid]

    def cal_get(self, req):
        c = self._cal(req)
        return ok({"id": c["id"], "timeZone": c["timeZone"]})

    def cal_create(self, req):
        cid = nid("cal")
        self.calendars[cid] = {"id": cid, "summary": _json(req)["summary"], "timeZone": "UTC",
                               "accessRole": "owner", "events": {}}
        return ok({"id": cid})

    def events(self, req):
        return ok({"items": list(self._cal(req)["events"].values())})

    def import_ev(self, req):
        body = _json(req)
        self.import_calls += 1
        assert "iCalUID" in body, "events.import requires iCalUID"
        eid = nid("ev")
        body["id"] = eid
        if body.get("originalStartTime"):
            masters = [e for e in self._cal(req)["events"].values()
                       if e.get("iCalUID") == body["iCalUID"] and e.get("recurrence")]
            assert masters, "exception imported before its series"
            body["recurringEventId"] = masters[0]["id"]
        self._cal(req)["events"][eid] = body
        return ok(body)

    def del_ev(self, req):
        eid = urlparse(req.url).path.rsplit("/", 1)[1]
        self._cal(req)["events"].pop(eid, None)
        return (204, {}, "")

    # -- drive --
    def _parse_q(self, q):
        parent = re.search(r"'([^']+)' in parents", q).group(1)
        name = re.search(r"name = '((?:[^'\\]|\\.)*)'", q)
        return parent, (name.group(1).replace("\\'", "'") if name else None), \
            "mimeType = 'application/vnd.google-apps.folder'" in q

    def list_files(self, req):
        q = _q(req)
        parent, name, folders_only = self._parse_q(q["q"])
        items = [f for f in self.children(parent) if not f.get("trashed")]
        if name is not None:
            items = [f for f in items if f["name"] == name]
        if folders_only:
            items = [f for f in items if f["mimeType"].endswith("folder")]
        return ok({"files": [{k: v for k, v in f.items() if k != "content"} for f in items]})

    def create_folder(self, req):
        body = _json(req)
        fid = self.add_folder(body["name"], body["parents"][0])
        return ok({"id": fid})

    def media(self, req):
        f = self.files[urlparse(req.url).path.rsplit("/", 1)[1]]
        return (200, {"Content-Type": "application/octet-stream"}, f["content"])

    def export(self, req):
        f = self.files[urlparse(req.url).path.split("/files/")[1].split("/")[0]]
        assert _q(req)["mimeType"].startswith("application/vnd.openxmlformats") or \
            _q(req)["mimeType"] == "application/pdf"
        return (200, {"Content-Type": _q(req)["mimeType"]}, b"EXPORTED:" + f["name"].encode())

    def list_perms(self, req):
        fid = urlparse(req.url).path.split("/files/")[1].split("/")[0]
        return ok({"permissions": self.perms.get(fid, [])})

    def add_perm(self, req):
        fid = urlparse(req.url).path.split("/files/")[1].split("/")[0]
        assert _q(req).get("sendNotificationEmail") == "false", "must not notify"
        p = _json(req)
        p["id"] = nid("p")
        self.perms.setdefault(fid, []).append(p)
        return ok(p)

    def upload(self, req):
        q = _q(req)
        if q["uploadType"] == "multipart":
            raw = req.body if isinstance(req.body, bytes) else req.body.encode()
            boundary = re.search(r"boundary=(\S+)", req.headers["Content-Type"]).group(1).encode()
            parts = raw.split(b"--" + boundary)
            meta = json.loads(parts[1].split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0])
            content = parts[2].split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0]
            return ok({"id": self._store(meta, content)})
        sid = nid("s")
        self.sessions[sid] = {"meta": _json(req), "data": b"",
                              "size": int(req.headers["X-Upload-Content-Length"])}
        return (200, {"Location": f"https://upload.fake/google/{sid}"}, "")

    def upload_chunk(self, req):
        sid = urlparse(req.url).path.rsplit("/", 1)[1]
        s = self.sessions[sid]
        start, end, total = map(int, re.match(r"bytes (\d+)-(\d+)/(\d+)",
                                              req.headers["Content-Range"]).groups())
        assert start == len(s["data"]) and (end - start + 1) % (256 * 1024) == 0 or end + 1 == total
        s["data"] += req.body if isinstance(req.body, bytes) else req.body.read()
        if len(s["data"]) < total:
            return (308, {"Range": f"bytes=0-{len(s['data']) - 1}"}, "")
        return ok({"id": self._store(s["meta"], s["data"])})

    def _store(self, meta, content):
        fid = self.add_file(meta["name"], meta["parents"][0], content,
                            meta.get("mimeType", "application/octet-stream"))
        self.files[fid]["modifiedTime"] = meta.get("modifiedTime", "")
        return fid

    def list_drives(self, req):
        return ok({"drives": [{"id": d["id"], "name": d["name"]} for d in self.drives.values()]})

    def create_drive(self, req):
        did = nid("drive")
        self.drives[did] = {"id": did, "name": _json(req)["name"]}
        self.files[did] = {"id": did, "name": _json(req)["name"],
                           "mimeType": "application/vnd.google-apps.folder", "parents": []}
        return ok(self.drives[did])


# =================================================================== Graph

class FakeGraph:
    G = "https://graph.microsoft.com/v1.0"

    def __init__(self, rsps: responses.RequestsMock, user: str = "me@contoso.com") -> None:
        self.user = user
        self.contacts: dict[str, dict] = {}
        self.folders: dict[str, dict] = {}
        self.calendars: dict[str, dict] = {"calA": {"id": "calA", "name": "Calendar",
                                                    "isDefaultCalendar": True, "canEdit": True,
                                                    "owner": {"address": user}, "events": {}}}
        self.deleted_instances: set[str] = set()
        self.patched_instances: dict[str, dict] = {}
        self.items: dict[str, dict] = {"root": {"id": "root", "name": "root", "folder": {},
                                                "parent": None}}
        self.perms: dict[str, list] = {}
        self.sessions: dict[str, dict] = {}
        self.groups: dict[str, dict] = {}
        self.created_with_attendees = 0
        self._register(rsps)

    def add_item(self, name, parent="root", content=b"", folder=False):
        iid = nid("i")
        self.items[iid] = {"id": iid, "name": name, "parent": parent,
                           **({"folder": {}} if folder else
                              {"file": {"mimeType": "application/octet-stream"},
                               "size": len(content), "content": content}),
                           "fileSystemInfo": {"lastModifiedDateTime": "2024-02-03T04:05:06Z"}}
        return iid

    def path_of(self, iid):
        parts, cur = [], self.items[iid]
        while cur["parent"] not in (None, "root"):
            cur = self.items[cur["parent"]]
            parts.insert(0, cur["name"])
        return "/".join(parts + [self.items[iid]["name"]])

    def _register(self, r: responses.RequestsMock) -> None:
        G = re.escape(self.G)
        U = r"(?:/me|/users/[^/]+)"
        cb = r.add_callback
        cb("GET", re.compile(G + U + r"/contacts(\?.*)?$"), self.list_contacts)
        cb("GET", re.compile(G + U + r"/contactFolders(\?.*)?$"), lambda q: ok({"value": list(self.folders.values())}))
        cb("GET", re.compile(G + U + r"/contactFolders/[^/]+/childFolders.*"), lambda q: ok({"value": []}))
        cb("GET", re.compile(G + U + r"/contactFolders/[^/]+/contacts.*"), self.folder_contacts)
        cb("POST", re.compile(G + U + r"/contacts$"), self.create_contact)
        cb("PATCH", re.compile(G + U + r"/contacts/[^/]+$"), self.update_contact)
        cb("GET", re.compile(G + U + r"/calendars(\?.*)?$"), self.list_cals)
        cb("POST", re.compile(G + U + r"/calendars$"), self.create_cal)
        cb("GET", re.compile(G + U + r"/calendars/[^/]+/events.*"), self.list_events)
        cb("POST", re.compile(G + U + r"/calendars/[^/]+/events$"), self.create_event)
        cb("GET", re.compile(G + U + r"/events/[^/]+/instances.*"), self.instances)
        cb("DELETE", re.compile(G + U + r"/events/[^/]+$"), self.delete_event)
        cb("PATCH", re.compile(G + U + r"/events/[^/]+$"), self.patch_event)
        cb("GET", re.compile(G + U + r"/drive(\?.*)?$"), lambda q: ok({"id": "d1"}))
        cb("GET", re.compile(G + r"/drives/[^/]+/root(\?.*)?$"), lambda q: ok({"id": "root"}))
        cb("GET", re.compile(G + r"/drives/[^/]+/items/[^/:]+/children.*"), self.children)
        cb("POST", re.compile(G + r"/drives/[^/]+/items/[^/:]+/children$"), self.make_folder)
        cb("GET", re.compile(G + r"/drives/[^/]+/items/[^/:]+:/[^:]+(\?.*)?$"), self.by_path)
        cb("PUT", re.compile(G + r"/drives/[^/]+/items/[^/:]+:/[^:]+:/content.*"), self.put_small)
        cb("POST", re.compile(G + r"/drives/[^/]+/items/[^/:]+:/[^:]+:/createUploadSession"), self.session)
        cb("PUT", re.compile(r"https://upload\.fake/graph/.*"), self.put_chunk)
        cb("GET", re.compile(G + r"/drives/[^/]+/items/[^/]+/content"), self.content)
        cb("PATCH", re.compile(G + r"/drives/[^/]+/items/[^/:]+$"), self.patch_item)
        cb("GET", re.compile(G + r"/drives/[^/]+/items/[^/]+/permissions.*"), self.list_perms)
        cb("POST", re.compile(G + r"/drives/[^/]+/items/[^/]+/invite"), self.invite)
        cb("POST", re.compile(G + r"/drives/[^/]+/items/[^/]+/createLink"), self.link)

    # -- contacts --
    def list_contacts(self, req):
        return ok({"value": [c for c in self.contacts.values() if not c.get("_folder")]})

    def folder_contacts(self, req):
        fid = urlparse(req.url).path.split("/contactFolders/")[1].split("/")[0]
        return ok({"value": [c for c in self.contacts.values() if c.get("_folder") == fid]})

    def create_contact(self, req):
        body = _json(req)
        assert len(body.get("emailAddresses", [])) <= 3, "Graph allows 3 email addresses"
        cid = nid("ct")
        self.contacts[cid] = {**body, "id": cid}
        return ok(self.contacts[cid], 201)

    def update_contact(self, req):
        cid = urlparse(req.url).path.rsplit("/", 1)[1]
        if cid not in self.contacts:
            return err(404, "ErrorItemNotFound")
        self.contacts[cid].update(_json(req))
        return ok(self.contacts[cid])

    # -- calendar --
    def list_cals(self, req):
        return ok({"value": [{k: v for k, v in c.items() if k != "events"}
                             for c in self.calendars.values()]})

    def create_cal(self, req):
        cid = nid("cal")
        self.calendars[cid] = {"id": cid, "name": _json(req)["name"], "canEdit": True,
                               "owner": {"address": self.user}, "events": {}}
        return ok(self.calendars[cid], 201)

    def _cal_id(self, req):
        return urlparse(req.url).path.split("/calendars/")[1].split("/")[0]

    def list_events(self, req):
        assert 'outlook.timezone="UTC"' in req.headers.get("Prefer", "")
        return ok({"value": list(self.calendars[self._cal_id(req)]["events"].values())})

    def create_event(self, req):
        body = _json(req)
        if body.get("attendees"):
            self.created_with_attendees += 1
        eid = nid("e")
        ev = {**body, "id": eid, "type": "seriesMaster" if body.get("recurrence") else
              "singleInstance", "iCalUId": f"uid-{eid}",
              "originalStartTimeZone": body["start"]["timeZone"]}
        self.calendars[self._cal_id(req)]["events"][eid] = ev
        return ok(ev, 201)

    def _find(self, eid):
        for c in self.calendars.values():
            if eid in c["events"]:
                return c["events"][eid]
        return None

    def _occurrences(self, master, lo, hi):
        from mailmigrator.cloud.recurrence import expand, graph_to_rrule, parse_local, zone
        tz = master["start"]["timeZone"]
        start = parse_local(master["start"]["dateTime"], tz).astimezone(zone(tz))
        end = parse_local(master["end"]["dateTime"], tz)
        duration = end - parse_local(master["start"]["dateTime"], tz)
        rule = graph_to_rrule(master["recurrence"])
        out = []
        for occ in expand(rule, start, hi.astimezone(zone(tz))):
            occ_utc = occ.astimezone(timezone.utc)
            if occ_utc < lo:
                continue
            out.append((occ_utc, duration))
        return out

    def instances(self, req):
        mid = urlparse(req.url).path.split("/events/")[1].split("/")[0]
        master = self._find(mid)
        q = _q(req)
        lo = datetime.fromisoformat(q["startDateTime"].replace("Z", "+00:00"))
        hi = datetime.fromisoformat(q["endDateTime"].replace("Z", "+00:00"))
        if lo.tzinfo is None:
            lo, hi = lo.replace(tzinfo=timezone.utc), hi.replace(tzinfo=timezone.utc)
        out = []
        for occ, duration in self._occurrences(master, lo, hi):
            iid = f"{mid}_{occ:%Y%m%dT%H%M}"
            if iid in self.deleted_instances:
                continue
            inst = {"id": iid, "type": "occurrence", "seriesMasterId": mid,
                    "originalStart": occ.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "subject": master.get("subject"), "isAllDay": False,
                    "start": {"dateTime": occ.strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"},
                    "end": {"dateTime": (occ + duration).strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"}}
            if iid in self.patched_instances:
                inst.update(self.patched_instances[iid])
                inst["type"] = "exception"
            out.append(inst)
        return ok({"value": out})

    def delete_event(self, req):
        eid = urlparse(req.url).path.rsplit("/", 1)[1]
        if "_" in eid:
            self.deleted_instances.add(eid)
        else:
            for c in self.calendars.values():
                c["events"].pop(eid, None)
        return (204, {}, "")

    def patch_event(self, req):
        eid = urlparse(req.url).path.rsplit("/", 1)[1]
        self.patched_instances[eid] = _json(req)
        return ok({"id": eid})

    # -- drive --
    def _item_id(self, req):
        return urlparse(req.url).path.split("/items/")[1].split("/")[0].split(":")[0]

    def children(self, req):
        parent = self._item_id(req)
        return ok({"value": [{k: v for k, v in i.items() if k != "content"}
                             for i in self.items.values() if i["parent"] == parent]})

    def make_folder(self, req):
        parent, body = self._item_id(req), _json(req)
        assert not re.search(r'["*:<>?/\\|]', body["name"])
        if any(i["parent"] == parent and i["name"].lower() == body["name"].lower()
               for i in self.items.values()):
            return err(409, "nameAlreadyExists")
        return ok({"id": self.add_item(body["name"], parent, folder=True)}, 201)

    def by_path(self, req):
        parent = self._item_id(req)
        name = unquote(urlparse(req.url).path.split(":/", 1)[1].rstrip(":"))
        for i in self.items.values():
            if i["parent"] == parent and i["name"].lower() == name.lower():
                return ok({"id": i["id"]})
        return err(404, "itemNotFound")

    def _put(self, parent, name, content):
        for i in list(self.items.values()):
            if i["parent"] == parent and i["name"].lower() == name.lower():
                del self.items[i["id"]]   # conflictBehavior=replace
        return self.add_item(name, parent, content)

    def put_small(self, req):
        parent = self._item_id(req)
        name = unquote(urlparse(req.url).path.split(":/", 1)[1].split(":/content")[0])
        body = req.body if isinstance(req.body, bytes) else req.body.encode()
        assert len(body) <= 4 * 1024 * 1024, "simple upload is limited to 4 MB"
        return ok({"id": self._put(parent, name, body)}, 201)

    def session(self, req):
        parent = self._item_id(req)
        name = unquote(urlparse(req.url).path.split(":/", 1)[1].split(":/createUploadSession")[0])
        sid = nid("u")
        self.sessions[sid] = {"parent": parent, "name": name, "data": b"",
                              "fs": (_json(req).get("item") or {}).get("fileSystemInfo")}
        return ok({"uploadUrl": f"https://upload.fake/graph/{sid}"})

    def put_chunk(self, req):
        assert "Authorization" not in req.headers, "upload URLs must not get the bearer token"
        sid = urlparse(req.url).path.rsplit("/", 1)[1]
        s = self.sessions[sid]
        start, end, total = map(int, re.match(r"bytes (\d+)-(\d+)/(\d+)",
                                              req.headers["Content-Range"]).groups())
        assert (end - start + 1) % (320 * 1024) == 0 or end + 1 == total
        s["data"] += req.body if isinstance(req.body, bytes) else req.body.read()
        if len(s["data"]) < total:
            return ok({"nextExpectedRanges": [f"{len(s['data'])}-"]}, 202)
        iid = self._put(s["parent"], s["name"], s["data"])
        if s["fs"]:
            self.items[iid]["fileSystemInfo"] = s["fs"]
        return ok({"id": iid}, 201)

    def content(self, req):
        return (200, {"Content-Type": "application/octet-stream"},
                self.items[self._item_id(req)]["content"])

    def patch_item(self, req):
        iid = self._item_id(req)
        self.items[iid].update(_json(req))
        return ok({"id": iid})

    def list_perms(self, req):
        return ok({"value": self.perms.get(self._item_id(req), [])})

    def invite(self, req):
        body = _json(req)
        assert body["sendInvitation"] is False, "must not send sharing emails"
        self.perms.setdefault(self._item_id(req), []).append(
            {"id": nid("p"), "roles": body["roles"],
             "grantedToV2": {"user": {"email": body["recipients"][0]["email"]}}})
        return ok({"value": []})

    def link(self, req):
        body = _json(req)
        self.perms.setdefault(self._item_id(req), []).append(
            {"id": nid("p"), "roles": ["read"], "link": {"scope": body["scope"],
                                                          "type": body["type"]}})
        return ok({"id": "l"}, 201)


def fake_token():
    return "test-token"


class Tok:
    """Token source for tests."""
    def __call__(self):
        return "test-token"

    def invalidate(self):
        pass


_ = timedelta  # used by callers computing windows
