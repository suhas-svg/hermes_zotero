"""Pure helpers that shape Zotero API objects into compact, model-friendly rows."""

from __future__ import annotations

import re
from typing import Any

from .client import strip_html

_ANNOTATED_TYPES = {"application/pdf", "application/epub+zip", "text/html"}


def creators_text(data: dict[str, Any], limit: int = 3) -> str:
    names = []
    for creator in data.get("creators") or []:
        if creator.get("creatorType", "author") not in {"author", "editor", "inventor", "presenter"}:
            continue
        names.append(creator.get("lastName") or creator.get("name") or "")
    names = [n for n in names if n]
    if len(names) > limit:
        return f"{names[0]} et al."
    return ", ".join(names)


def last_names(data: dict[str, Any]) -> list[str]:
    return [
        (c.get("lastName") or c.get("name") or "").strip()
        for c in data.get("creators") or []
        if c.get("lastName") or c.get("name")
    ]


def year_of(item: dict[str, Any]) -> str:
    parsed = str((item.get("meta") or {}).get("parsedDate") or "")
    if parsed[:4].isdigit():
        return parsed[:4]
    match = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", str((item.get("data") or {}).get("date") or ""))
    return match.group(1) if match else ""


def summary(item: dict[str, Any]) -> dict[str, Any]:
    data = item.get("data") or {}
    meta = item.get("meta") or {}
    row = {
        "key": item.get("key") or data.get("key"),
        "title": data.get("title") or data.get("caseName") or data.get("subject") or "(untitled)",
        "authors": meta.get("creatorSummary") or creators_text(data),
        "year": year_of(item),
        "type": data.get("itemType"),
        "publication": data.get("publicationTitle") or data.get("proceedingsTitle")
        or data.get("bookTitle") or data.get("repository") or data.get("publisher") or "",
        "doi": data.get("DOI") or "",
        "tags": [t.get("tag") for t in data.get("tags") or [] if t.get("tag")],
        "children": meta.get("numChildren", 0),
    }
    return {k: v for k, v in row.items() if v not in ("", None, [])}


def detail(item: dict[str, Any]) -> dict[str, Any]:
    data = item.get("data") or {}
    row = summary(item)
    row["creators"] = [
        {"name": " ".join(p for p in (c.get("firstName"), c.get("lastName")) if p) or c.get("name", ""),
         "role": c.get("creatorType", "author")}
        for c in data.get("creators") or []
    ]
    for field in ("abstractNote", "date", "url", "volume", "issue", "pages", "ISBN", "archiveID"):
        if data.get(field):
            row[field] = data[field]
    return row


def note_row(item: dict[str, Any]) -> dict[str, Any]:
    data = item.get("data") or {}
    return {"key": item.get("key"), "text": strip_html(data.get("note"))}


def attachment_row(item: dict[str, Any]) -> dict[str, Any]:
    data = item.get("data") or {}
    row = {
        "key": item.get("key"),
        "title": data.get("title") or data.get("filename") or "",
        "content_type": data.get("contentType") or "",
        "link_mode": data.get("linkMode") or "",
    }
    return {k: v for k, v in row.items() if v}


def is_readable_attachment(item: dict[str, Any]) -> bool:
    data = item.get("data") or {}
    return data.get("itemType") == "attachment" and data.get("contentType") in _ANNOTATED_TYPES


def annotation_row(item: dict[str, Any]) -> dict[str, Any]:
    data = item.get("data") or {}
    row = {
        "type": data.get("annotationType"),
        "text": (data.get("annotationText") or "").strip(),
        "comment": strip_html(data.get("annotationComment")),
        "page": data.get("annotationPageLabel") or "",
        "color": data.get("annotationColor") or "",
        "_sort": data.get("annotationSortIndex") or "",
    }
    return {k: v for k, v in row.items() if v}


def passages(text: str, terms: list[str], *, radius: int = 320, per_term: int = 4) -> list[dict[str, Any]]:
    """Context windows around each term, overlapping windows merged, in text order.

    PDF text breaks lines (and hyphenates words) mid-phrase, so search a
    whitespace-normalised copy; offsets refer to that copy.
    """
    text = re.sub(r"\s+", " ", re.sub(r"(?<=[a-z])-\s*\n\s*(?=[a-z])", "", text))
    terms = [" ".join(term.split()) for term in terms]
    spans: list[tuple[int, int, str]] = []
    lowered = text.lower()
    for term in terms:
        needle = term.strip().lower()
        if not needle:
            continue
        start, found = 0, 0
        while found < per_term:
            index = lowered.find(needle, start)
            if index < 0:
                break
            spans.append((max(0, index - radius), min(len(text), index + len(needle) + radius), term))
            start, found = index + len(needle), found + 1
    spans.sort()
    merged: list[list[Any]] = []
    for begin, end, term in spans:
        if merged and begin <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
            merged[-1][2].add(term)
        else:
            merged.append([begin, end, {term}])
    return [
        {"offset": begin, "terms": sorted(found), "text": " ".join(text[begin:end].split())}
        for begin, end, found in merged
    ]


# ---------- citation extraction ----------

_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"<>()\[\]]+", re.IGNORECASE)
_PAREN_RE = re.compile(r"\(([^()]*?\b(?:1[5-9]\d\d|20\d\d)[a-z]?[^()]*)\)")
_NAME = r"[A-Z][A-Za-z'À-ſ\-]+(?:\s(?:van|von|de|der|da|di|le|la)\s[A-Z][A-Za-z'\-]+)?"
_PART_RE = re.compile(
    rf"(?P<name>(?:{_NAME}\s)?{_NAME})(?:\s+et\s+al\.?|\s*(?:,|and|&)\s*{_NAME})*,?\s*(?P<year>(?:1[5-9]\d\d|20\d\d))[a-z]?"
)
_NARRATIVE_RE = re.compile(
    rf"\b(?P<name>{_NAME})(?:\s+et\s+al\.?|\s+(?:and|&)\s+{_NAME})?\s+\((?P<year>(?:1[5-9]\d\d|20\d\d))[a-z]?"
)


def extract_citations(text: str) -> list[dict[str, str]]:
    """Find DOIs and author-year citations in prose; deduplicated, in order."""
    found: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, value: str, raw: str, year: str = "") -> None:
        marker = (kind, f"{value.lower()}|{year}")
        if marker not in seen:
            seen.add(marker)
            row = {"kind": kind, "raw": raw.strip()}
            row.update({"doi": value} if kind == "doi" else {"author": value, "year": year})
            found.append(row)

    for match in _DOI_RE.finditer(text):
        doi = match.group(0).rstrip(".,;")
        add("doi", doi, doi)
    for group in _PAREN_RE.finditer(text):
        for part in group.group(1).split(";"):
            hit = _PART_RE.search(part)
            if hit:
                add("author_year", hit.group("name").split()[-1], part, hit.group("year"))
    for hit in _NARRATIVE_RE.finditer(text):
        add("author_year", hit.group("name").split()[-1], hit.group(0) + ")", hit.group("year"))
    return found
