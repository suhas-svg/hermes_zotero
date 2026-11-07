"""Zotero desktop client: the local Web-API mirror, the connector, and the full-text cache.

Standard library only. Every request goes to the configured Zotero endpoint
(loopback by default); metadata lookups for new papers live in ``resolve.py``.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any

DEFAULT_URL = "http://127.0.0.1:23119"
_MAX_ERROR_BODY = 400
_ENABLE_HINT = (
    "In Zotero open Settings -> Advanced and tick 'Allow other applications on "
    "this computer to communicate with Zotero', then run `hermes zotero status`."
)


class ZoteroError(RuntimeError):
    """A user-actionable Zotero failure."""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


@dataclass(frozen=True)
class ZoteroConfig:
    url: str = DEFAULT_URL
    timeout: float = 15.0
    data_dir: str = ""


def strip_html(value: Any) -> str:
    """Turn Zotero note/bibliography HTML into readable plain text."""
    text = str(value or "")
    text = re.sub(r"(?is)<(style|script)\b.*?</\1>", "", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(?:div|p|li|tr|h[1-6])>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def default_data_dir() -> Path:
    return Path.home() / "Zotero"


class ZoteroClient:
    """Thin wrapper over Zotero desktop's HTTP server on port 23119."""

    def __init__(self, config: ZoteroConfig) -> None:
        self.config = config
        self.url = config.url.rstrip("/")
        self.api = f"{self.url}/api/users/0"
        # Zotero listens on loopback; never route those calls through a proxy
        # inherited from the user's shell.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # ---------- transport ----------

    def _open(self, request: urllib.request.Request, what: str) -> tuple[bytes, dict[str, str]]:
        try:
            with self._opener.open(request, timeout=self.config.timeout) as response:
                return response.read(), {k.lower(): v for k, v in response.headers.items()}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:_MAX_ERROR_BODY].strip()
            raise _http_error(exc.code, body, what) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ZoteroError(
                f"Could not reach Zotero at {self.url}: {exc}",
                hint="Open the Zotero desktop app and leave it running. " + _ENABLE_HINT,
            ) from exc

    def get(self, path: str, **params: Any) -> Any:
        """GET a local-API path; JSON responses are decoded, text is returned as str."""
        query = urllib.parse.urlencode(
            {k: v for k, v in params.items() if v not in (None, "")}, doseq=True
        )
        url = f"{self.api}{path}" + (f"?{query}" if query else "")
        request = urllib.request.Request(
            url, headers={"Zotero-API-Version": "3", "Accept": "application/json"}
        )
        raw, headers = self._open(request, path)
        text = raw.decode("utf-8", errors="replace")
        if "json" in headers.get("content-type", ""):
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                raise ZoteroError(f"Zotero returned invalid JSON for {path}.") from exc
        return text

    def connector(self, endpoint: str, payload: dict[str, Any] | None = None) -> Any:
        request = urllib.request.Request(
            f"{self.url}/connector/{endpoint}",
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Zotero-Connector-API-Version": "3",
            },
            method="POST",
        )
        raw, _ = self._open(request, f"connector/{endpoint}")
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    # ---------- library reads ----------

    def ping(self) -> bool:
        request = urllib.request.Request(f"{self.url}/connector/ping")
        raw, _ = self._open(request, "connector/ping")
        return b"Zotero" in raw

    def items(self, path: str = "/items/top", **params: Any) -> list[dict[str, Any]]:
        result = self.get(path, format="json", **params)
        if isinstance(result, dict):
            return [result]
        if not isinstance(result, list):
            raise ZoteroError(f"Zotero returned an unexpected response for {path}.")
        return [item for item in result if isinstance(item, dict)]

    def item(self, key: str, **params: Any) -> dict[str, Any]:
        items = self.items(f"/items/{_key(key)}", **params)
        if not items:
            raise ZoteroError(f"No Zotero item with key {key}.")
        return items[0]

    def children(self, key: str) -> list[dict[str, Any]]:
        return self.items(f"/items/{_key(key)}/children")

    def collections(self) -> list[dict[str, Any]]:
        return self.items("/collections")

    def export(self, keys: list[str], fmt: str) -> str:
        result = self.get("/items", itemKey=",".join(_key(k) for k in keys), format=fmt)
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

    def fulltext(self, attachment_key: str) -> str:
        """Indexed text for an attachment: the local API first, then Zotero's cache file."""
        try:
            result = self.get(f"/items/{_key(attachment_key)}/fulltext")
        except ZoteroError:
            result = None
        if isinstance(result, dict) and result.get("content"):
            return str(result["content"])
        cache = self._data_dir() / "storage" / _key(attachment_key) / ".zotero-ft-cache"
        if cache.is_file():
            return cache.read_text(encoding="utf-8", errors="replace")
        return ""

    def _data_dir(self) -> Path:
        return Path(self.config.data_dir).expanduser() if self.config.data_dir else default_data_dir()

    # ---------- writes (connector) ----------

    def selected_collection(self) -> dict[str, Any]:
        result = self.connector("getSelectedCollection")
        return result if isinstance(result, dict) else {}

    def save_items(self, items: list[dict[str, Any]], session_id: str, uri: str) -> None:
        self.connector("saveItems", {"items": items, "sessionID": session_id, "uri": uri})

    def save_attachment(self, pdf: bytes, *, session_id: str, parent_id: str, title: str, url: str) -> None:
        """Upload a PDF as a child of an item saved in the same connector session.

        Zotero's connector does not download attachment URLs passed to
        saveItems; the browser connector uploads the bytes itself, so do the same.
        """
        metadata = {"sessionID": session_id, "parentItemID": parent_id, "title": title, "url": url}
        request = urllib.request.Request(
            f"{self.url}/connector/saveAttachment",
            data=pdf,
            headers={"Content-Type": "application/pdf", "X-Metadata": json.dumps(metadata)},
            method="POST",
        )
        self._open(request, "connector/saveAttachment")


def _key(value: str) -> str:
    key = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{8}", key):
        raise ZoteroError(
            f"{value!r} is not a Zotero item key.",
            hint="Item keys are 8 characters, e.g. ABCD2345; get them from zotero_search.",
        )
    return key


def _http_error(code: int, body: str, what: str) -> ZoteroError:
    if code == 403 and "local api" in body.lower():
        return ZoteroError("Zotero's local API is turned off.", hint=_ENABLE_HINT)
    if code == 404:
        return ZoteroError(f"Zotero has nothing at {what}: {body or 'not found'}.")
    if code == 501:
        return ZoteroError(
            f"This Zotero version does not support {what}.",
            hint="Update the Zotero desktop app to the latest release.",
        )
    return ZoteroError(f"Zotero returned HTTP {code} for {what}: {body}")
