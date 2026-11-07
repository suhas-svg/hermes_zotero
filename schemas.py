"""JSON Schemas for the Zotero tools."""

from __future__ import annotations

from typing import Any

_KEY = {"type": "string", "description": "8-character Zotero item key from zotero_search, e.g. ABCD2345."}
_NO_ARGS = {"type": "object", "properties": {}, "additionalProperties": False}

STATUS_SCHEMA: dict[str, Any] = {
    "description": (
        "Check that Zotero desktop is reachable and list the library's collections "
        "with item counts. Call this first when the library is unknown."
    ),
    "parameters": _NO_ARGS,
}

SEARCH_SCHEMA: dict[str, Any] = {
    "description": (
        "Search the user's Zotero library. Returns top-level items with key, title, "
        "authors, year, type, DOI, tags and how many notes/attachments each has. "
        "Use the returned keys with the other zotero_* tools."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Words to match. Empty lists the most recently added items."},
            "everything": {
                "type": "boolean",
                "description": "Also match abstracts, notes and indexed PDF text (slower). Default false: title, creator, year.",
            },
            "tag": {"type": "string", "description": "Only items with this tag."},
            "collection": {"type": "string", "description": "Only items in this collection (name or key)."},
            "item_type": {"type": "string", "description": "Zotero item type such as journalArticle, book, preprint."},
            "limit": {"type": "integer", "description": "Maximum items to return (1-50, default 15)."},
        },
        "additionalProperties": False,
    },
}

ITEM_SCHEMA: dict[str, Any] = {
    "description": (
        "Read one Zotero item in depth: full metadata and abstract, the user's notes, "
        "attachments, and every PDF highlight and comment with its page label. "
        "Use this to ground summaries in what the user actually marked up."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "key": _KEY,
            "include_annotations": {"type": "boolean", "description": "Include PDF highlights and comments (default true)."},
        },
        "required": ["key"],
        "additionalProperties": False,
    },
}

FULLTEXT_SCHEMA: dict[str, Any] = {
    "description": (
        "Read the indexed full text of an item's PDF. Pass `find` to get only the "
        "passages around those words (best for long papers); otherwise returns the "
        "start of the text up to max_chars."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Item key or its PDF attachment's key."},
            "find": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Words or phrases to locate; each match comes back with surrounding context.",
            },
            "max_chars": {"type": "integer", "description": "Maximum characters returned (500-40000, default 12000)."},
        },
        "required": ["key"],
        "additionalProperties": False,
    },
}

CITE_SCHEMA: dict[str, Any] = {
    "description": (
        "Format citations for Zotero items with Zotero's own citation engine. "
        "Use this for every reference you write; never type a citation from memory. "
        "`output` bibliography/citation returns styled text (APA, Chicago, IEEE, ...); "
        "bibtex, biblatex, ris and csljson return export text."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "keys": {"type": "array", "items": {"type": "string"}, "description": "Item keys, in the order to cite."},
            "output": {
                "type": "string",
                "enum": ["bibliography", "citation", "bibtex", "biblatex", "ris", "csljson"],
                "description": "Default bibliography.",
            },
            "style": {
                "type": "string",
                "description": "CSL style id installed in Zotero, e.g. apa, chicago-author-date, ieee, modern-language-association, nature. Defaults to the plugin setting.",
            },
        },
        "required": ["keys"],
        "additionalProperties": False,
    },
}

ADD_SCHEMA: dict[str, Any] = {
    "description": (
        "Add papers to the user's Zotero library by DOI or arXiv id. Looks up the "
        "metadata (Crossref / arXiv), skips papers already in the library, and saves "
        "the rest into the collection currently selected in Zotero; arXiv papers get "
        "their PDF attached. Confirm the list with the user before calling."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "identifiers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "DOIs, arXiv ids, or doi.org / arxiv.org URLs (max 20).",
            },
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags to put on every saved item."},
            "attach_pdf": {"type": "boolean", "description": "Download and attach the open-access PDF for arXiv papers (default true)."},
        },
        "required": ["identifiers"],
        "additionalProperties": False,
    },
}

VERIFY_SCHEMA: dict[str, Any] = {
    "description": (
        "Check the citations in a draft against the Zotero library. Finds DOIs and "
        "author-year citations such as (Vaswani et al., 2017) or Smith and Lee (2020), "
        "and reports which match a library item (with its key) and which do not. "
        "Use before handing over any text that cites sources."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The draft text to check."},
        },
        "required": ["text"],
        "additionalProperties": False,
    },
}
