"""Tool, slash-command, and CLI handlers for the Zotero plugin."""

from __future__ import annotations

import json
import logging
import shlex
import uuid
from functools import wraps
from typing import Any, Callable

from . import library
from .client import DEFAULT_URL, ZoteroClient, ZoteroConfig, ZoteroError, strip_html
from .resolve import Fetch, http_fetch, parse_identifier, resolve

logger = logging.getLogger(__name__)

_MAX_LIST = 50
_MAX_ADD = 20
_MAX_PDF_BYTES = 60 * 1024 * 1024
_EXPORT_FORMATS = {"bibtex", "biblatex", "ris", "csljson"}


def _ok(payload: dict[str, Any]) -> str:
    return json.dumps({"success": True, **payload}, ensure_ascii=False)


def _error(message: str, *, hint: str = "") -> str:
    payload = {"error": message}
    if hint:
        payload["hint"] = hint
    return json.dumps(payload, ensure_ascii=False)


def _guard(fn: Callable[..., str]) -> Callable[..., str]:
    @wraps(fn)
    def wrapper(self: "ZoteroTools", args: dict[str, Any], **kwargs: Any) -> str:
        try:
            return fn(self, args or {})
        except ZoteroError as exc:
            return _error(str(exc), hint=exc.hint)
        except Exception as exc:  # tool handlers must never raise
            logger.exception("zotero tool %s failed", fn.__name__)
            return _error(f"Unexpected Zotero plugin error: {exc}")

    return wrapper


def _clamp_int(raw: Any, default: int, low: int, high: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _strings(raw: Any, name: str) -> list[str]:
    if raw is None:
        return []
    values = [raw] if isinstance(raw, str) else raw
    if not isinstance(values, (list, tuple)):
        raise ZoteroError(f"{name} must be a list of strings.")
    return list(dict.fromkeys(str(v).strip() for v in values if str(v).strip()))


class ZoteroTools:
    """Stateless handlers bound to a plugin context."""

    def __init__(self, ctx: Any, fetch: Fetch = http_fetch) -> None:
        self.ctx = ctx
        self._fetch = fetch

    # ---------- config / client ----------

    def _get(self, key: str, default: Any) -> Any:
        try:
            value = self.ctx.get_config(key, default)
        except Exception:
            logger.debug("get_config failed for %s", key, exc_info=True)
            return default
        return default if value is None else value

    def _client(self) -> ZoteroClient:
        url = str(self._get("zotero_url", DEFAULT_URL)).strip() or DEFAULT_URL
        if not url.startswith(("http://", "https://")):
            raise ZoteroError("zotero_url must start with http:// or https://.",
                              hint=f"The default is {DEFAULT_URL}.")
        try:
            timeout = float(self._get("request_timeout", 15.0) or 15.0)
        except (TypeError, ValueError) as exc:
            raise ZoteroError("request_timeout must be a number of seconds.") from exc
        return ZoteroClient(ZoteroConfig(
            url=url,
            timeout=max(1.0, min(120.0, timeout)),
            data_dir=str(self._get("data_dir", "") or ""),
        ))

    def _style(self, args: dict[str, Any]) -> str:
        return str(args.get("style") or self._get("citation_style", "apa") or "apa").strip()

    def _collection_key(self, client: ZoteroClient, wanted: str) -> str:
        collections = client.collections()
        for col in collections:
            data = col.get("data") or {}
            if wanted.upper() == str(col.get("key")) or wanted.lower() == str(data.get("name", "")).lower():
                return str(col.get("key"))
        names = ", ".join(sorted((c.get("data") or {}).get("name", "") for c in collections)) or "(none)"
        raise ZoteroError(f"No Zotero collection named {wanted!r}.", hint=f"Collections: {names}")

    # ---------- model-callable tools ----------

    @_guard
    def status(self, args: dict[str, Any]) -> str:
        client = self._client()
        client.ping()
        collections = [
            {"key": c.get("key"), "name": (c.get("data") or {}).get("name"),
             "items": (c.get("meta") or {}).get("numItems", 0),
             "parent": (c.get("data") or {}).get("parentCollection") or None}
            for c in client.collections()
        ]
        recent = client.items("/items/top", limit=5, sort="dateAdded", direction="desc")
        return _ok({
            "connected": True,
            "url": client.url,
            "collections": collections,
            "recently_added": [library.summary(i) for i in recent],
        })

    @_guard
    def search(self, args: dict[str, Any]) -> str:
        client = self._client()
        query = str(args.get("query") or "").strip()
        limit = _clamp_int(args.get("limit", 15), 15, 1, _MAX_LIST)
        path = "/items/top"
        if args.get("collection"):
            path = f"/collections/{self._collection_key(client, str(args['collection']).strip())}/items/top"
        params: dict[str, Any] = {
            "q": query,
            "qmode": "everything" if args.get("everything") else "titleCreatorYear",
            "tag": str(args.get("tag") or "").strip(),
            "itemType": str(args.get("item_type") or "").strip(),
            "limit": limit,
        }
        if not query:
            params.update(sort="dateAdded", direction="desc")
        items = client.items(path, **params)[:limit]
        rows = [library.summary(i) for i in items]
        payload: dict[str, Any] = {"query": query, "count": len(rows), "items": rows}
        if not rows:
            payload["message"] = (
                "Nothing matched. Try everything=true to search abstracts and PDF text, "
                "or zotero_add to bring a paper into the library."
            )
        return _ok(payload)

    @_guard
    def item(self, args: dict[str, Any]) -> str:
        client = self._client()
        item = client.item(str(args.get("key") or ""))
        row = library.detail(item)
        notes, attachments, annotations = [], [], []
        for child in client.children(row["key"]):
            kind = (child.get("data") or {}).get("itemType")
            if kind == "note":
                notes.append(library.note_row(child))
            elif kind == "attachment":
                attachments.append(library.attachment_row(child))
                if args.get("include_annotations", True) and library.is_readable_attachment(child):
                    annotations.extend(self._annotations(client, child))
        annotations.sort(key=lambda a: a.pop("_sort", ""))
        row.update(notes=notes, attachments=attachments, annotations=annotations)
        return _ok({"item": row})

    def _annotations(self, client: ZoteroClient, attachment: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for child in client.children(str(attachment.get("key"))):
            if (child.get("data") or {}).get("itemType") == "annotation":
                rows.append({**library.annotation_row(child), "attachment": attachment.get("key")})
        return rows

    @_guard
    def fulltext(self, args: dict[str, Any]) -> str:
        client = self._client()
        item = client.item(str(args.get("key") or ""))
        max_chars = _clamp_int(args.get("max_chars", 12000), 12000, 500, 40000)
        sources = [item] if library.is_readable_attachment(item) else [
            c for c in client.children(str(item.get("key"))) if library.is_readable_attachment(c)
        ]
        texts = [(str(s.get("key")), client.fulltext(str(s.get("key")))) for s in sources]
        texts = [(k, t) for k, t in texts if t.strip()]
        if not texts:
            raise ZoteroError(
                f"Zotero has no indexed text for {item.get('key')}.",
                hint="Attach a PDF to the item, or right-click the PDF in Zotero and choose 'Reindex Item'.",
            )
        terms = _strings(args.get("find"), "find")
        if terms:
            return _ok(self._find(texts, terms, max_chars, library.summary(item)))
        key, text = texts[0]
        return _ok({
            "item": library.summary(item), "attachment": key, "total_chars": len(text),
            "truncated": len(text) > max_chars, "text": text[:max_chars],
        })

    @staticmethod
    def _find(texts: list[tuple[str, str]], terms: list[str], max_chars: int, item: dict[str, Any]) -> dict[str, Any]:
        found, used = [], 0
        for key, text in texts:
            for passage in library.passages(text, terms):
                if used + len(passage["text"]) > max_chars:
                    break
                found.append({**passage, "attachment": key})
                used += len(passage["text"])
        missing = [t for t in terms if not any(t in p["terms"] for p in found)]
        return {"item": item, "terms": terms, "passages": found, "not_found": missing}

    @_guard
    def cite(self, args: dict[str, Any]) -> str:
        keys = [k.upper() for k in _strings(args.get("keys"), "keys")]
        if not keys:
            raise ZoteroError("keys must list at least one Zotero item key.")
        output = str(args.get("output") or "bibliography").strip().lower()
        client = self._client()
        if output in _EXPORT_FORMATS:
            return _ok({"output": output, "keys": keys, "text": client.export(keys, output)})
        if output not in {"bibliography", "citation"}:
            raise ZoteroError("output must be bibliography, citation, bibtex, biblatex, ris or csljson.")
        style = self._style(args)
        field = "bib" if output == "bibliography" else "citation"
        items = client.items("/items", itemKey=",".join(keys), include=f"data,{field}", style=style)
        by_key = {str(i.get("key")): i for i in items}
        missing = [k for k in keys if k not in by_key]
        entries = [
            {"key": k, "text": strip_html(by_key[k].get(field)), "title": (by_key[k].get("data") or {}).get("title")}
            for k in keys if k in by_key
        ]
        payload: dict[str, Any] = {
            "output": output, "style": style, "entries": entries,
            "text": "\n".join(e["text"] for e in entries),
        }
        if missing:
            payload["missing_keys"] = missing
        return _ok(payload)

    @_guard
    def add(self, args: dict[str, Any]) -> str:
        identifiers = _strings(args.get("identifiers"), "identifiers")
        if not identifiers:
            raise ZoteroError("identifiers must list at least one DOI or arXiv id.")
        if len(identifiers) > _MAX_ADD:
            raise ZoteroError(f"Too many identifiers ({len(identifiers)}); the limit is {_MAX_ADD} per call.")
        tags = _strings(args.get("tags"), "tags")
        client = self._client()
        client.ping()
        results, to_save = [], []
        for raw in identifiers:
            results.append(self._prepare(client, raw, tags, to_save))
        attached = 0
        if to_save:
            session = uuid.uuid4().hex
            pdfs = [(item["id"], item.pop("attachments", [])) for item in to_save]
            client.save_items(to_save, session, to_save[0].get("url", "https://doi.org/"))
            if args.get("attach_pdf", True):
                attached = self._attach_pdfs(client, session, pdfs, results)
        target = client.selected_collection() if to_save else {}
        return _ok({
            "saved": sum(r["status"] == "saved" for r in results),
            "pdfs_attached": attached,
            "already_in_library": sum(r["status"] == "exists" for r in results),
            "failed": sum(r["status"] == "error" for r in results),
            "saved_to": target.get("name") or target.get("libraryName") or "",
            "results": results,
        })

    def _prepare(self, client: ZoteroClient, raw: str, tags: list[str], to_save: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            kind, ident = parse_identifier(raw)
            doi = ident if kind == "doi" else f"10.48550/arXiv.{ident}"
            existing = self._existing(client, doi, ident)
            if existing:
                return {"input": raw, "status": "exists", **existing}
            item = resolve(raw, self._fetch)
        except ZoteroError as exc:
            return {"input": raw, "status": "error", "error": str(exc)}
        item["id"] = uuid.uuid4().hex[:8]
        if tags:
            item["tags"] = [{"tag": t} for t in tags]
        to_save.append(item)
        return {"input": raw, "status": "saved", "title": item.get("title"),
                "authors": library.creators_text(item), "year": (item.get("date") or "")[:4]}

    def _attach_pdfs(self, client: ZoteroClient, session: str,
                     pdfs: list[tuple[str, list[dict[str, Any]]]], results: list[dict[str, Any]]) -> int:
        saved_rows = [r for r in results if r["status"] == "saved"]
        attached = 0
        for (parent_id, attachments), row in zip(pdfs, saved_rows):
            for attachment in attachments:
                try:
                    pdf = self._fetch(attachment["url"])
                    if not pdf.startswith(b"%PDF") or len(pdf) > _MAX_PDF_BYTES:
                        raise ZoteroError("the download was not a PDF under 60 MB")
                    client.save_attachment(pdf, session_id=session, parent_id=parent_id,
                                           title=attachment["title"], url=attachment["url"])
                    row["pdf"] = "attached"
                    attached += 1
                except ZoteroError as exc:
                    row["pdf"] = f"not attached: {exc}"
        return attached

    @staticmethod
    def _existing(client: ZoteroClient, *needles: str) -> dict[str, Any]:
        for needle in needles:
            for hit in client.items("/items/top", q=needle, qmode="everything", limit=5):
                data = hit.get("data") or {}
                haystack = " ".join(str(data.get(f, "")) for f in ("DOI", "url", "archiveID", "extra")).lower()
                if needle.lower() in haystack:
                    return library.summary(hit)
        return {}

    @_guard
    def verify(self, args: dict[str, Any]) -> str:
        text = str(args.get("text") or "")
        if not text.strip():
            raise ZoteroError("text must contain the draft to check.")
        client = self._client()
        checks = [self._check(client, cite) for cite in library.extract_citations(text)]
        unmatched = [c for c in checks if not c.get("matches")]
        return _ok({
            "citations_found": len(checks),
            "matched": len(checks) - len(unmatched),
            "unmatched": len(unmatched),
            "checks": checks,
            "message": (
                "Every citation matches an item in the library." if checks and not unmatched else
                "No citations found in the text." if not checks else
                "Some citations are not in the library: find the source, add it with zotero_add, or remove the claim."
            ),
        })

    @staticmethod
    def _check(client: ZoteroClient, cite: dict[str, str]) -> dict[str, Any]:
        if cite["kind"] == "doi":
            hits = ZoteroTools._existing(client, cite["doi"])
            return {**cite, "matches": [hits] if hits else []}
        matches = []
        for hit in client.items("/items/top", q=cite["author"], qmode="titleCreatorYear", limit=25):
            data = hit.get("data") or {}
            names = [n.lower() for n in library.last_names(data)]
            if cite["author"].lower() in names and library.year_of(hit) == cite["year"]:
                matches.append(library.summary(hit))
        return {**cite, "matches": matches}

    # ---------- /zotero slash command ----------

    def slash(self, raw_args: str, **kwargs: Any) -> str:
        del kwargs
        try:
            parts = shlex.split(raw_args or "")
        except ValueError as exc:
            return f"Could not parse /zotero arguments: {exc}\n\n{_slash_help()}"
        command = parts[0].lower() if parts else "status"
        rest = parts[1:]
        handlers = {"status": self._slash_status, "search": self._slash_search, "cite": self._slash_cite}
        if command not in handlers:
            return _slash_help() if command in {"help", "-h", "--help"} else (
                f"Unknown /zotero subcommand: {command}\n\n{_slash_help()}")
        try:
            return handlers[command](rest)
        except ZoteroError as exc:
            return f"Zotero error: {exc}" + (f"\nHint: {exc.hint}" if exc.hint else "")
        except Exception as exc:  # a slash handler must return text, not crash the turn
            logger.exception("/zotero failed")
            return f"Unexpected Zotero error: {exc}"

    def _slash_status(self, _rest: list[str]) -> str:
        client = self._client()
        client.ping()
        collections = client.collections()
        names = ", ".join((c.get("data") or {}).get("name", "") for c in collections[:12]) or "(none)"
        recent = client.items("/items/top", limit=3, sort="dateAdded", direction="desc")
        lines = [f"Zotero is connected at {client.url}.", f"Collections ({len(collections)}): {names}"]
        if recent:
            lines.append("Recently added:")
            lines += [f"  {_line(library.summary(i))}" for i in recent]
        lines.append("Try `/zotero search <words>` or ask Hermes about your papers.")
        return "\n".join(lines)

    def _slash_search(self, rest: list[str]) -> str:
        query = " ".join(rest).strip()
        if not query:
            return "Usage: /zotero search <words>"
        items = self._client().items("/items/top", q=query, qmode="titleCreatorYear", limit=10)
        if not items:
            return f"No Zotero items match {query!r}."
        return "\n".join([f"Matches for {query!r}:"] + [f"  {_line(library.summary(i))}" for i in items[:10]])

    def _slash_cite(self, rest: list[str]) -> str:
        if not rest:
            return "Usage: /zotero cite <KEY> [KEY...]"
        result = json.loads(self.cite({"keys": rest}))
        if result.get("error"):
            return f"Zotero error: {result['error']}"
        return result["text"] or "Zotero returned no citation text."

    # ---------- hermes zotero CLI ----------

    def cli(self, args: Any) -> int:
        command = getattr(args, "zotero_command", None) or "status"
        try:
            if command == "status":
                print(self._slash_status([]))
                return 0
            if command == "search":
                print(self._slash_search(list(getattr(args, "query", []) or [])))
                return 0
            if command == "cite":
                result = json.loads(self.cite({
                    "keys": getattr(args, "keys", []),
                    "output": getattr(args, "format", "bibliography"),
                    "style": getattr(args, "style", None),
                }))
                if result.get("error"):
                    print(f"Zotero error: {result['error']}")
                    return 1
                print(result["text"])
                return 0
        except ZoteroError as exc:
            print(f"Zotero error: {exc}")
            if exc.hint:
                print(f"Hint: {exc.hint}")
            return 1
        except Exception as exc:  # a CLI command must not traceback on bad config
            logger.exception("hermes zotero failed")
            print(f"Unexpected Zotero error: {exc}")
            return 1
        print("Usage: hermes zotero <status|search WORDS|cite KEY...>")
        return 2


def _setup_cli(subparser: Any) -> None:
    subs = subparser.add_subparsers(dest="zotero_command")
    subs.add_parser("status", help="Check Zotero and list collections")
    search = subs.add_parser("search", help="Search titles, creators and years")
    search.add_argument("query", nargs="+", help="Words to search for")
    cite = subs.add_parser("cite", help="Print a bibliography or export for item keys")
    cite.add_argument("keys", nargs="+", help="Zotero item keys")
    cite.add_argument("--style", default=None, help="CSL style id, e.g. apa, ieee")
    cite.add_argument("--format", default="bibliography",
                      choices=["bibliography", "citation", "bibtex", "biblatex", "ris", "csljson"])


def _line(row: dict[str, Any]) -> str:
    who = row.get("authors") or "?"
    year = f" ({row['year']})" if row.get("year") else ""
    return f"[{row.get('key')}] {who}{year}: {row.get('title')}"


def _slash_help() -> str:
    return (
        "Usage: /zotero [status|search WORDS|cite KEY...]\n"
        "  status         Check Zotero, list collections and recent items.\n"
        "  search WORDS   Search titles, creators and years.\n"
        "  cite KEY...    Bibliography entries in your default style.\n"
        "Requires Zotero desktop running with Settings -> Advanced -> "
        "'Allow other applications on this computer to communicate with Zotero'."
    )
