"""A small Notion API client (stdlib only) for the parts config-sync uses."""
from __future__ import annotations

import base64
import json
import os
import re
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

VERSION = "2026-03-11"
FULL_NAME, DELTA_NAME = "전체본", "변경분"
DEFAULT_BASE = "https://api.notion.com"
TIMEOUT = 60
RETRIES = 3
MAX_WAIT = 30
SECRET_RE = re.compile(r"^[\x21-\x7e]{1,500}$")
ID_RE = re.compile(r"([0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12})")


class NotionError(RuntimeError):
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(message or code)
        self.status = status
        self.code = code


def page_id(text: str) -> str:
    """The 32-hex page id inside a Notion page address or a (dashed) id."""
    found = ID_RE.findall((text or "").split("?")[0].split("#")[0])
    if not found:
        raise NotionError(0, "bad_page", "Notion 페이지 주소에서 페이지 id를 찾지 못했어요")
    return found[-1].replace("-", "").lower()


def plain(notion_id: str) -> str:
    return (notion_id or "").replace("-", "").lower()


def text(row: dict, name: str) -> str:
    prop = (row.get("properties") or {}).get(name) or {}
    parts = prop.get("title") if prop.get("type") == "title" else prop.get("rich_text")
    return "".join(t.get("plain_text", "") for t in parts or [])


def select(row: dict, name: str) -> Optional[str]:
    prop = (row.get("properties") or {}).get(name) or {}
    return (prop.get("select") or {}).get("name")


def files(row: dict, name: str) -> List[dict]:
    prop = (row.get("properties") or {}).get(name) or {}
    out = []
    for f in prop.get("files") or []:
        hosted = f.get("file") or f.get("external") or {}
        out.append({"name": f.get("name", ""), "url": hosted.get("url", "")})
    return out


def rich(value: str) -> dict:
    return {"rich_text": [{"text": {"content": value}}]} if value else {"rich_text": []}


def title(value: str) -> dict:
    return {"title": [{"text": {"content": value[:1900]}}]}


def _base(base: Optional[str] = None) -> str:
    return (base or os.environ.get("CLAUDE_SYNC_NOTION_API") or DEFAULT_BASE).rstrip("/")


def authorize_url(client_id: str, redirect_uri: str, state: str, base: Optional[str] = None) -> str:
    """Notion's consent screen, where the person signs in and picks the pages to share."""
    return _base(base) + "/v1/oauth/authorize?" + urlencode(
        {"client_id": client_id, "response_type": "code", "owner": "user", "redirect_uri": redirect_uri, "state": state})


# The codes OAuth defines (RFC 6749): anything else in an error field is not shown, it could be anything.
OAUTH_ERRORS = {"invalid_request", "invalid_client", "invalid_grant", "unauthorized_client", "unsupported_grant_type",
                "invalid_scope", "access_denied", "server_error", "temporarily_unavailable"}
OAUTH_MESSAGES = {
    "invalid_grant": "Notion 연결이 만료됐어요. 'Notion으로 연결'을 다시 해 주세요",
    "invalid_client": "Notion 공개 연결 정보(client id/secret)가 맞지 않아요",
}


def oauth_token(client_id: str, client_secret: str, body: dict, base: Optional[str] = None,
                sleep: Callable[[float], Any] = time.sleep) -> dict:
    """POST /v1/oauth/token: a code (grant_type=authorization_code) or a refresh token for a new pair."""
    basic = base64.b64encode(("%s:%s" % (client_id, client_secret)).encode()).decode()
    headers = {"Authorization": "Basic " + basic, "Content-Type": "application/json", "Accept": "application/json",
               "Notion-Version": VERSION}
    for attempt in range(RETRIES + 1):
        try:
            with urlopen(Request(_base(base) + "/v1/oauth/token", data=json.dumps(body).encode(), method="POST",
                                 headers=headers), timeout=TIMEOUT) as r:
                out = json.loads(r.read() or b"{}")
            if not out.get("access_token") or not out.get("refresh_token"):
                raise NotionError(0, "bad_token", "Notion이 토큰을 주지 않았어요")
            return out
        except HTTPError as e:
            if e.code == 429 and attempt < RETRIES:
                sleep(_retry_after(e.headers.get("Retry-After")))
                continue
            try:
                raw = json.loads(e.read() or b"{}").get("error")
            except (ValueError, AttributeError):
                raw = None
            code = raw if raw in OAUTH_ERRORS else "http_%d" % e.code
            # Fixed texts only: the response could echo the secret or a token.
            raise NotionError(e.code, code, OAUTH_MESSAGES.get(code, "Notion 연결에 실패했어요 (%d %s)" % (e.code, code))) \
                from None
        except (URLError, OSError, ValueError) as e:
            raise NotionError(0, "offline", "Notion에 연결하지 못했어요: %s" % _reason(e).replace(client_secret, "***")[:200]) \
                from None
    raise NotionError(429, "rate_limited")  # not reached


class Client:
    def __init__(self, secret: str, base: Optional[str] = None, sleep: Callable[[float], Any] = time.sleep):
        self._secret = secret
        self.base = _base(base)
        self._sleep = sleep

    def _scrub(self, message: str) -> str:
        return message.replace(self._secret, "***") if self._secret else message

    def request(self, method: str, path: str, body: Optional[dict] = None, *,
                data: Optional[bytes] = None, content_type: str = "application/json") -> dict:
        # Checked before anything is sent: a malformed key would otherwise be echoed by header errors.
        if not SECRET_RE.match(self._secret or ""):
            raise NotionError(0, "bad_secret", "Notion 비밀 키 형식이 올바르지 않아요. 공백·줄바꿈 없이 다시 붙여 넣어 주세요")
        if body is not None:
            data = json.dumps(body).encode()
        headers = {"Authorization": "Bearer " + self._secret, "Notion-Version": VERSION}
        if data is not None:
            headers["Content-Type"] = content_type
        for attempt in range(RETRIES + 1):
            try:
                with urlopen(Request(self.base + path, data=data, method=method, headers=headers),
                             timeout=TIMEOUT) as r:
                    raw = r.read()
                return json.loads(raw or b"{}")
            except HTTPError as e:
                if e.code == 429 and attempt < RETRIES:
                    self._sleep(_retry_after(e.headers.get("Retry-After")))
                    continue
                # The secret goes before any cut: a cut through it would leave a piece unmasked.
                err = _error(e)
                raise NotionError(err.status, err.code, self._scrub(str(err))[:300]) from None
            except (URLError, OSError, ValueError) as e:
                raise NotionError(0, "offline", "Notion에 연결하지 못했어요: %s" % self._scrub(_reason(e))[:200]) from None
        raise NotionError(429, "rate_limited")  # not reached

    def children(self, block_id: str) -> List[dict]:
        out: List[dict] = []
        cursor = None
        while True:
            q = "?page_size=100" + ("&start_cursor=%s" % cursor if cursor else "")
            r = self.request("GET", "/v1/blocks/%s/children%s" % (plain(block_id), q))
            out += r.get("results") or []
            cursor = r.get("next_cursor")
            if not r.get("has_more") or not cursor:
                return out

    def search_pages(self) -> List[dict]:
        """Pages this connection can reach (for a login: the ones the person picked), with titles."""
        out: List[dict] = []
        cursor = None
        while True:
            body: Dict[str, Any] = {"filter": {"property": "object", "value": "page"}, "page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            r = self.request("POST", "/v1/search", body)
            for page in r.get("results") or []:
                if (page.get("parent") or {}).get("type") in ("database_id", "data_source_id"):
                    continue  # a database row (such as a sync row), not a page to pick
                name = next((text(page, k) for k, v in (page.get("properties") or {}).items()
                             if (v or {}).get("type") == "title"), "")
                out.append({"id": plain(page["id"]), "title": name})
            cursor = r.get("next_cursor")
            if not r.get("has_more") or not cursor:
                return out

    def create_database(self, parent_page: str, name: str, properties: dict) -> Tuple[str, str]:
        r = self.request("POST", "/v1/databases", {
            "parent": {"type": "page_id", "page_id": plain(parent_page)},
            "title": [{"type": "text", "text": {"content": name}}],
            "initial_data_source": {"properties": properties},
        })
        return plain(r["id"]), plain(r["data_sources"][0]["id"])

    def data_source_of(self, database: str) -> str:
        r = self.request("GET", "/v1/databases/%s" % plain(database))
        sources = r.get("data_sources") or []
        if not sources:
            raise NotionError(404, "no_data_source", "데이터베이스에 데이터 소스가 없어요")
        return plain(sources[0]["id"])

    def query(self, data_source: str, *, descending: bool = False, page_size: int = 100,
              limit: Optional[int] = None, stop: Optional[Callable[[List[dict]], bool]] = None) -> List[dict]:
        """Rows by created_time; `stop(rows so far)` returning True ends the paging."""
        out: List[dict] = []
        cursor = None
        while True:
            body: Dict[str, Any] = {"sorts": [{"timestamp": "created_time",
                                               "direction": "descending" if descending else "ascending"}],
                                    "page_size": page_size}
            if cursor:
                body["start_cursor"] = cursor
            r = self.request("POST", "/v1/data_sources/%s/query" % plain(data_source), body)
            results = r.get("results") or []
            out += results
            if limit is not None and len(out) >= limit:
                return out[:limit]
            if stop is not None and stop(out):
                return out
            cursor = r.get("next_cursor")
            if not r.get("has_more") or not cursor:
                return out

    def upload(self, name: str, data: bytes) -> str:
        up = self.request("POST", "/v1/file_uploads", {"filename": name, "content_type": "application/octet-stream"})
        boundary = "claude-sync-%s" % uuid.uuid4().hex
        body = (("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
                 "Content-Type: application/octet-stream\r\n\r\n") % (boundary, name)).encode() \
            + data + ("\r\n--%s--\r\n" % boundary).encode()
        self.request("POST", "/v1/file_uploads/%s/send" % plain(up["id"]), data=body,
                     content_type="multipart/form-data; boundary=%s" % boundary)
        return up["id"]

    def create_row(self, data_source: str, properties: dict) -> str:
        r = self.request("POST", "/v1/pages", {"parent": {"type": "data_source_id", "data_source_id": plain(data_source)},
                                               "properties": properties})
        return plain(r["id"])

    def download(self, url: str) -> bytes:
        # Hosted file addresses are presigned: no Authorization header (it would be refused).
        try:
            with urlopen(Request(url, method="GET"), timeout=TIMEOUT) as r:
                return r.read()
        except HTTPError as e:
            raise NotionError(e.code, "download_failed", "첨부 파일을 받지 못했어요 (%d)" % e.code) from None
        except (URLError, OSError) as e:
            raise NotionError(0, "offline", "첨부 파일을 받지 못했어요: %s" % _reason(e)[:200]) from None


def _retry_after(value: Optional[str]) -> float:
    try:
        return max(0.0, min(float(value or 1), MAX_WAIT))
    except ValueError:
        return 1.0


def _reason(e: Exception) -> str:
    return str(getattr(e, "reason", e))


MESSAGES = {
    401: "Notion 비밀 키가 맞지 않아요. 연결(integration)의 비밀 키를 다시 넣어 주세요",
    403: "이 Notion 연결에 권한이 없어요. 페이지의 연결(Connections)에 추가했는지 확인하세요",
    404: "Notion 페이지를 찾지 못했어요. 페이지에 연결(Connections)을 추가했는지 확인하세요",
    429: "Notion 요청이 너무 많아요. 잠시 뒤 다시 시도합니다",
}


def _error(e: HTTPError) -> NotionError:
    try:
        payload = json.loads(e.read() or b"{}")
    except ValueError:
        payload = {}
    code = payload.get("code") or "http_%d" % e.code
    detail = payload.get("message") or ""
    message = MESSAGES.get(e.code) or ("Notion 오류 %d: %s" % (e.code, detail or code))
    return NotionError(e.code, code, message)
