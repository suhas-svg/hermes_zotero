"""Zotero research plugin entry point."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .schemas import (
    ADD_SCHEMA,
    CITE_SCHEMA,
    FULLTEXT_SCHEMA,
    ITEM_SCHEMA,
    SEARCH_SCHEMA,
    STATUS_SCHEMA,
    VERIFY_SCHEMA,
)
from .tools import ZoteroTools, _setup_cli

logger = logging.getLogger(__name__)

_TOOLS = (
    ("zotero_status", STATUS_SCHEMA, "status", "Check Zotero and list collections."),
    ("zotero_search", SEARCH_SCHEMA, "search", "Search the Zotero library."),
    ("zotero_item", ITEM_SCHEMA, "item", "Read an item's metadata, notes and PDF highlights."),
    ("zotero_fulltext", FULLTEXT_SCHEMA, "fulltext", "Read or search an item's indexed PDF text."),
    ("zotero_cite", CITE_SCHEMA, "cite", "Format citations with Zotero's citation engine."),
    ("zotero_add", ADD_SCHEMA, "add", "Add papers to Zotero by DOI or arXiv id."),
    ("zotero_verify", VERIFY_SCHEMA, "verify", "Check a draft's citations against the library."),
)


def register(ctx: Any) -> None:
    """Register the plugin's tools, slash command, CLI command, and skill."""
    tools = ZoteroTools(ctx)
    for name, schema, method, description in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="zotero",
            schema=schema,
            handler=getattr(tools, method),
            description=description,
        )

    ctx.register_command(
        "zotero",
        handler=tools.slash,
        description="Zotero status, search and citations",
        args_hint="[status|search WORDS|cite KEY...]",
    )
    ctx.register_cli_command(
        name="zotero",
        help="Work with the local Zotero library",
        setup_fn=_setup_cli,
        handler_fn=tools.cli,
    )

    skills_dir = Path(__file__).parent / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.exists():
                ctx.register_skill(child.name, skill_md)

    logger.debug("zotero: registered %d tools, /zotero, hermes zotero, and bundled skills", len(_TOOLS))
