"""Turn a DOI or arXiv id into a Zotero item.

The only outbound calls the plugin makes: Crossref (DOIs) and the arXiv export
API, and only when ``zotero_add`` runs. Both are public, keyless services.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Callable

from .client import ZoteroError, strip_html

CROSSREF_URL = "https://api.crossref.org/works/"
ARXIV_URL = "https://export.arxiv.org/api/query"
_USER_AGENT = "hermes-zotero/1.0 (+https://github.com/suhas-svg/hermes_zotero)"
_ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.IGNORECASE)
_ARXIV_RE = re.compile(
    r"(?:arxiv[:\s]*|arxiv\.org/(?:abs|pdf)/)?(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?",
    re.IGNORECASE,
)

_CROSSREF_TYPES = {
    "journal-article": "journalArticle",
    "proceedings-article": "conferencePaper",
    "book": "book",
    "monograph": "book",
    "edited-book": "book",
    "book-chapter": "bookSection",
    "posted-content": "preprint",
    "dissertation": "thesis",
    "report": "report",
    "dataset": "dataset",
}

Fetch = Callable[[str], bytes]


def parse_identifier(raw: str) -> tuple[str, str]:
    """Return ("doi"|"arxiv", normalised id) or raise ZoteroError."""
    text = str(raw or "").strip()
    if not text:
        raise ZoteroError("Empty identifier.")
    arxiv_doi = re.search(r"10\.48550/arxiv\.(\S+)", text, re.IGNORECASE)
    if arxiv_doi:
        return "arxiv", re.sub(r"v\d+$", "", arxiv_doi.group(1))
    doi = _DOI_RE.search(text)
    if doi:
        return "doi", doi.group(1).rstrip(".,;)")
    arxiv = _ARXIV_RE.fullmatch(text) or (
        _ARXIV_RE.search(text) if "arxiv" in text.lower() else None
    )
    if arxiv:
        return "arxiv", arxiv.group(1)
    raise ZoteroError(
        f"Could not read {text!r} as a DOI or arXiv id.",
        hint="Pass a DOI like 10.1038/nature14539 or an arXiv id like 1706.03762.",
    )


def http_fetch(url: str, timeout: float = 60.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ZoteroError(f"No record found at {url}.") from exc
        raise ZoteroError(f"Metadata lookup failed with HTTP {exc.code}: {url}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ZoteroError(f"Metadata lookup failed: {exc}") from exc


def resolve(raw: str, fetch: Fetch = http_fetch) -> dict[str, Any]:
    kind, ident = parse_identifier(raw)
    if kind == "doi":
        return crossref_item(ident, fetch)
    return arxiv_item(ident, fetch)


def crossref_item(doi: str, fetch: Fetch = http_fetch) -> dict[str, Any]:
    raw = fetch(CROSSREF_URL + urllib.parse.quote(doi, safe="/"))
    try:
        message = json.loads(raw.decode("utf-8"))["message"]
    except (ValueError, KeyError, UnicodeDecodeError) as exc:
        raise ZoteroError(f"Crossref returned an unreadable record for {doi}.") from exc
    item_type = _CROSSREF_TYPES.get(message.get("type", ""), "journalArticle")
    item: dict[str, Any] = {
        "itemType": item_type,
        "title": _first(message.get("title")),
        "creators": [_crossref_creator(a) for a in message.get("author") or [] if a.get("family") or a.get("name")],
        "date": _crossref_date(message),
        "DOI": message.get("DOI") or doi,
        "url": message.get("URL") or f"https://doi.org/{doi}",
        "abstractNote": strip_html(message.get("abstract")),
        "volume": message.get("volume", ""),
        "issue": message.get("issue", ""),
        "pages": message.get("page", ""),
        "publisher": message.get("publisher", ""),
        "ISSN": _first(message.get("ISSN")),
        "language": message.get("language", ""),
    }
    container = _first(message.get("container-title"))
    container_field = {
        "journalArticle": "publicationTitle",
        "conferencePaper": "proceedingsTitle",
        "bookSection": "bookTitle",
    }.get(item_type)
    if container_field and container:
        item[container_field] = container
    return _clean(item)


def arxiv_item(arxiv_id: str, fetch: Fetch = http_fetch) -> dict[str, Any]:
    raw = fetch(f"{ARXIV_URL}?{urllib.parse.urlencode({'id_list': arxiv_id})}")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ZoteroError(f"arXiv returned an unreadable record for {arxiv_id}.") from exc
    entry = root.find("a:entry", _ATOM)
    title = entry.findtext("a:title", "", _ATOM).strip() if entry is not None else ""
    if entry is None or not title or title.lower() == "error":
        raise ZoteroError(f"arXiv has no paper with id {arxiv_id}.")
    authors = [
        _split_name(a.findtext("a:name", "", _ATOM))
        for a in entry.findall("a:author", _ATOM)
    ]
    item = {
        "itemType": "preprint",
        "title": re.sub(r"\s+", " ", title),
        "creators": [a for a in authors if a.get("lastName")],
        "date": entry.findtext("a:published", "", _ATOM)[:10],
        "abstractNote": re.sub(r"\s+", " ", entry.findtext("a:summary", "", _ATOM)).strip(),
        "repository": "arXiv",
        "archiveID": f"arXiv:{arxiv_id}",
        "DOI": entry.findtext("arxiv:doi", "", _ATOM) or f"10.48550/arXiv.{arxiv_id}",
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "attachments": [{
            "title": "arXiv Fulltext PDF",
            "url": f"https://arxiv.org/pdf/{arxiv_id}",
            "mimeType": "application/pdf",
        }],
    }
    return _clean(item)


def _first(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def _crossref_creator(author: dict[str, Any]) -> dict[str, str]:
    if author.get("family"):
        return {"creatorType": "author", "firstName": author.get("given", ""), "lastName": author["family"]}
    return {"creatorType": "author", "name": author.get("name", "")}


def _crossref_date(message: dict[str, Any]) -> str:
    for field in ("published-print", "published-online", "issued", "created"):
        parts = ((message.get(field) or {}).get("date-parts") or [[None]])[0]
        if parts and parts[0]:
            return "-".join(f"{int(p):02d}" if i else str(p) for i, p in enumerate(parts))
    return ""


def _split_name(name: str) -> dict[str, str]:
    name = name.strip()
    if " " not in name:
        return {"creatorType": "author", "firstName": "", "lastName": name}
    first, last = name.rsplit(" ", 1)
    return {"creatorType": "author", "firstName": first, "lastName": last}


def _clean(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if v not in ("", None, [])}
