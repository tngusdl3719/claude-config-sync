"""A small in-memory stand-in for the parts of the Notion API config-sync uses."""
from __future__ import annotations

import base64
import json
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional

TOKEN = "ntn_fake_secret_for_tests"
CLIENT_ID = "0f0f0f0f-1111-2222-3333-444455556666"
CLIENT_SECRET = "secret_fake_oauth_client_secret"
# A few of the extensions the File Upload API accepts; a name without one is refused (as the real API does).
UPLOAD_EXTENSIONS = {"gz", "zip", "tar", "txt", "json", "pdf", "png"}


def dashed(hex_id: str) -> str:
    """Notion answers with hyphenated ids and accepts either form."""
    h = hex_id.replace("-", "")
    return "%s-%s-%s-%s-%s" % (h[:8], h[8:12], h[12:16], h[16:20], h[20:])


class FakeNotion:
    def __init__(self, max_file_bytes: int = 5 * 1024 * 1024):
        self.max_file_bytes = max_file_bytes
        self.pages: Dict[str, dict] = {}          # page id -> {"children": [block]}
        self.databases: Dict[str, dict] = {}      # db id -> {"parent", "title", "data_source"}
        self.data_sources: Dict[str, dict] = {}   # ds id -> {"properties", "rows": [page ids]}
        self.rows: Dict[str, dict] = {}           # row page id -> {"created_time", "properties"}
        self.uploads: Dict[str, dict] = {}        # upload id -> {"status", "data", "name"}
        self.files: Dict[str, bytes] = {}         # file key -> bytes (served at /files/<key>)
        self.requests: List[str] = []
        self.throttle_next = 0                    # answer this many requests with 429 first
        self.fail_next = None                     # (status, body) for the next authorized request
        self.auth_headers: List[str] = []
        self.tokens = {TOKEN}                     # access tokens the API accepts
        self.codes: Dict[str, dict] = {}          # OAuth code -> {"pages", "redirect_uri", "used"}
        self.refresh_tokens: Dict[str, List[str]] = {}  # refresh token -> granted pages
        self.grants: Dict[str, List[str]] = {}    # access token -> granted pages
        self.token_requests: List[dict] = []
        self._serial = 0
        self._clock = 0
        self.rows_per_minute = 3
        self.order = 0                            # insertion counter, hidden from clients
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def add_page(self, title: str = "동기화") -> str:
        page_id = uuid.uuid4().hex
        self.pages[page_id] = {"children": [], "title": title}
        return page_id

    def issue_code(self, pages: List[str], redirect_uri: str) -> str:
        """What Notion's consent screen hands the browser after the person picks pages."""
        code = "code_" + uuid.uuid4().hex
        self.codes[code] = {"pages": list(pages), "redirect_uri": redirect_uri, "used": False}
        return code

    def expire(self, access_token: str) -> None:
        self.tokens.discard(access_token)

    def _new_pair(self, pages: List[str]) -> dict:
        self._serial += 1
        access, refresh = "ntn_oauth_%d_%s" % (self._serial, uuid.uuid4().hex[:8]), "nrt_%d_%s" % (
            self._serial, uuid.uuid4().hex[:8])
        self.tokens.add(access)
        self.grants[access] = pages
        self.refresh_tokens[refresh] = pages
        return {"access_token": access, "refresh_token": refresh, "token_type": "bearer", "bot_id": "bot",
                "workspace_id": "ws", "workspace_name": "내 워크스페이스", "owner": {"type": "user"},
                "duplicated_template_id": None}

    def _now(self) -> str:
        # Like Notion, created_time has minute precision: several rows share one value.
        self._clock += 1
        minute = self._clock // self.rows_per_minute
        return "2027-01-15T%02d:%02d:00.000Z" % (8 + minute // 60, minute % 60)

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code: int, body: Optional[dict] = None, raw: Optional[bytes] = None, headers=None):
                data = raw if raw is not None else json.dumps(body or {}).encode()
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json" if raw is None else "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _body(self) -> bytes:
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def _token(self) -> str:
                header = self.headers.get("Authorization", "")
                return header[len("Bearer "):] if header.startswith("Bearer ") else ""

            def _authorized(self) -> bool:
                fake.auth_headers.append(self.headers.get("Authorization", ""))
                if self._token() not in fake.tokens:
                    self._send(401, {"object": "error", "code": "unauthorized"})
                    return False
                if not self.headers.get("Notion-Version"):
                    self._send(400, {"object": "error", "code": "missing_version"})
                    return False
                if fake.fail_next is not None:
                    status, body = fake.fail_next
                    fake.fail_next = None
                    self._send(status, body)
                    return False
                if fake.throttle_next > 0:
                    fake.throttle_next -= 1
                    self._send(429, {"object": "error", "code": "rate_limited"}, headers={"Retry-After": "0"})
                    return False
                return True

            def do_GET(self):
                fake.requests.append("GET " + self.path)
                m = re.match(r"^/files/([0-9a-f]+)$", self.path)
                if m:
                    data = fake.files.get(m.group(1))
                    return self._send(200, raw=data) if data is not None else self._send(404, {})
                if not self._authorized():
                    return
                m = re.match(r"^/v1/blocks/([0-9a-f-]+)/children", self.path)
                if m:
                    page = fake.pages.get(m.group(1).replace("-", ""))
                    if page is None:
                        return self._send(404, {"object": "error", "code": "object_not_found"})
                    return self._send(200, {"results": page["children"], "has_more": False, "next_cursor": None})
                m = re.match(r"^/v1/databases/([0-9a-f-]+)$", self.path)
                if m:
                    db = fake.databases.get(m.group(1).replace("-", ""))
                    if db is None:
                        return self._send(404, {"object": "error", "code": "object_not_found"})
                    return self._send(200, {"object": "database", "id": dashed(m.group(1)),
                                            "data_sources": [{"id": dashed(db["data_source"]), "name": db["title"]}]})
                self._send(404, {"object": "error", "code": "not_found"})

            def do_POST(self):
                fake.requests.append("POST " + self.path)
                raw = self._body()
                if self.path == "/v1/oauth/token":
                    return self._oauth(raw)
                if not self._authorized():
                    return
                if self.path == "/v1/search":
                    granted = fake.grants.get(self._token())
                    ids = granted if granted is not None else list(fake.pages)
                    results = [{"object": "page", "id": dashed(pid), "parent": {"type": "workspace", "workspace": True},
                                "properties": {"title": {"type": "title",
                                                         "title": [{"plain_text": fake.pages[pid].get("title", "")}]}}}
                               for pid in ids if pid in fake.pages]
                    # As in Notion, the rows of a database under a reachable page are pages too.
                    for db in fake.databases.values():
                        if db["parent"] in ids:
                            results += [fake._row_json(dict(fake.rows[r], id=r))
                                        for r in fake.data_sources[db["data_source"]]["rows"]]
                    return self._send(200, {"results": results, "has_more": False, "next_cursor": None})
                if self.path == "/v1/databases":
                    body = json.loads(raw)
                    parent = body["parent"]["page_id"].replace("-", "")
                    if parent not in fake.pages:
                        return self._send(404, {"object": "error", "code": "object_not_found"})
                    db_id, ds_id = uuid.uuid4().hex, uuid.uuid4().hex
                    title = "".join(t["text"]["content"] for t in body.get("title", []))
                    fake.databases[db_id] = {"parent": parent, "title": title, "data_source": ds_id}
                    fake.data_sources[ds_id] = {"properties": body["initial_data_source"]["properties"], "rows": []}
                    fake.pages[parent]["children"].append({"object": "block", "id": dashed(db_id),
                                                           "type": "child_database", "child_database": {"title": title}})
                    return self._send(200, {"object": "database", "id": dashed(db_id),
                                            "data_sources": [{"id": dashed(ds_id), "name": title}]})
                m = re.match(r"^/v1/data_sources/([0-9a-f-]+)/query$", self.path)
                if m:
                    ds = fake.data_sources.get(m.group(1).replace("-", ""))
                    if ds is None:
                        return self._send(404, {"object": "error", "code": "object_not_found"})
                    body = json.loads(raw or b"{}")
                    rows = [dict(fake.rows[r], id=r) for r in ds["rows"]]
                    # Rows of the same minute come back in no particular order (here: newest first).
                    rows.sort(key=lambda r: (r["created_time"], -r["order"]))
                    if (body.get("sorts") or [{}])[0].get("direction") == "descending":
                        rows.reverse()
                    size = int(body.get("page_size") or 100)
                    start = int(body.get("start_cursor") or 0)
                    page = rows[start:start + size]
                    more = start + size < len(rows)
                    results = [fake._row_json(r) for r in page]
                    return self._send(200, {"results": results, "has_more": more,
                                            "next_cursor": str(start + size) if more else None})
                if self.path == "/v1/file_uploads":
                    body = json.loads(raw or b"{}")
                    name = body.get("filename", "")
                    if "." not in name or name.rsplit(".", 1)[1].lower() not in UPLOAD_EXTENSIONS:
                        return self._send(400, {"object": "error", "code": "validation_error",
                                                "message": "Provided `filename` has an extension that is not "
                                                           "supported for the File Upload API."})
                    up = uuid.uuid4().hex
                    fake.uploads[up] = {"status": "pending", "data": None, "name": body.get("filename", "file")}
                    return self._send(200, {"object": "file_upload", "id": dashed(up), "status": "pending"})
                m = re.match(r"^/v1/file_uploads/([0-9a-f-]+)/send$", self.path)
                if m:
                    up = fake.uploads.get(m.group(1).replace("-", ""))
                    if up is None or up["status"] != "pending":
                        return self._send(400, {"object": "error", "code": "validation_error"})
                    data = fake._multipart_file(self.headers.get("Content-Type", ""), raw)
                    if data is None:
                        return self._send(400, {"object": "error", "code": "validation_error"})
                    if len(data) > fake.max_file_bytes:
                        return self._send(400, {"object": "error", "code": "validation_error",
                                                "message": "file too large"})
                    up.update(status="uploaded", data=data)
                    return self._send(200, {"object": "file_upload", "id": dashed(m.group(1)), "status": "uploaded"})
                if self.path == "/v1/pages":
                    body = json.loads(raw)
                    ds_id = body["parent"]["data_source_id"].replace("-", "")
                    ds = fake.data_sources.get(ds_id)
                    if ds is None:
                        return self._send(404, {"object": "error", "code": "object_not_found"})
                    props = body["properties"]
                    for name, prop in props.items():
                        if "files" in prop:
                            stored = []
                            for f in prop["files"]:
                                up = fake.uploads.get(f["file_upload"]["id"].replace("-", ""))
                                if up is None or up["status"] != "uploaded":
                                    return self._send(400, {"object": "error", "code": "validation_error"})
                                key = uuid.uuid4().hex
                                fake.files[key] = up["data"]
                                up["status"] = "attached"
                                stored.append({"name": f.get("name", up["name"]), "type": "file",
                                               "file": {"url": "%s/files/%s" % (fake.base, key),
                                                        "expiry_time": "2099-01-01T00:00:00.000Z"}})
                            props[name] = {"files": stored}
                    row_id = uuid.uuid4().hex
                    fake.order += 1
                    fake.rows[row_id] = {"created_time": fake._now(), "properties": props, "order": fake.order,
                                         "data_source": ds_id}
                    ds["rows"].append(row_id)
                    return self._send(200, {"object": "page", "id": dashed(row_id)})
                self._send(404, {"object": "error", "code": "not_found"})

            def _oauth(self, raw: bytes):
                expected = "Basic " + base64.b64encode(("%s:%s" % (CLIENT_ID, CLIENT_SECRET)).encode()).decode()
                if self.headers.get("Authorization") != expected:
                    return self._send(401, {"error": "invalid_client"})
                body = json.loads(raw or b"{}")
                fake.token_requests.append(body)
                if body.get("grant_type") == "authorization_code":
                    grant = fake.codes.get(body.get("code", ""))
                    if grant is None or grant["used"] or grant["redirect_uri"] != body.get("redirect_uri"):
                        return self._send(400, {"error": "invalid_grant"})
                    grant["used"] = True
                    return self._send(200, fake._new_pair(grant["pages"]))
                if body.get("grant_type") == "refresh_token":
                    pages = fake.refresh_tokens.pop(body.get("refresh_token", ""), None)
                    if pages is None:
                        return self._send(400, {"error": "invalid_grant"})
                    return self._send(200, fake._new_pair(pages))
                return self._send(400, {"error": "unsupported_grant_type"})

        return Handler

    @staticmethod
    def _multipart_file(content_type: str, raw: bytes) -> Optional[bytes]:
        m = re.search(r"boundary=([^;]+)", content_type)
        if not m:
            return None
        boundary = ("--" + m.group(1).strip('"')).encode()
        for part in raw.split(boundary):
            head, sep, body = part.partition(b"\r\n\r\n")
            if sep and b'name="file"' in head:
                return body[:-2] if body.endswith(b"\r\n") else body
        return None

    def _row_json(self, row: dict) -> dict:
        props = {}
        for name, prop in row["properties"].items():
            if "title" in prop:
                props[name] = {"type": "title", "title": [{"plain_text": t["text"]["content"]} for t in prop["title"]]}
            elif "rich_text" in prop:
                props[name] = {"type": "rich_text",
                               "rich_text": [{"plain_text": t["text"]["content"]} for t in prop["rich_text"]]}
            elif "select" in prop:
                props[name] = {"type": "select", "select": prop["select"]}
            elif "files" in prop:
                props[name] = {"type": "files", "files": prop["files"]}
        return {"object": "page", "id": dashed(row["id"]), "created_time": row["created_time"], "properties": props,
                "parent": {"type": "data_source_id", "data_source_id": dashed(row["data_source"])}}
