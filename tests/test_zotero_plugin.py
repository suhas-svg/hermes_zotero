"""Behaviour tests for the Zotero plugin.

A fake Zotero desktop server binds an ephemeral loopback port, so these tests
exercise the real HTTP client, the local-API JSON shapes and the connector
without a Zotero install. Metadata lookups use an injected fetch.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def _load_plugin():
    name = "zotero_plugin_under_test"
    spec = importlib.util.spec_from_file_location(
        name, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PLUGIN = _load_plugin()
library = sys.modules["zotero_plugin_under_test.library"]
resolve_mod = sys.modules["zotero_plugin_under_test.resolve"]


class FakeCtx:
    def __init__(self, config: dict | None = None) -> None:
        self.config = dict(config or {})
        self.tools: dict[str, dict] = {}
        self.commands: dict[str, dict] = {}
        self.cli: dict[str, dict] = {}
        self.skills: dict[str, Path] = {}

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def register_tool(self, **kwargs):
        self.tools[kwargs["name"]] = kwargs

    def register_command(self, name, **kwargs):
        self.commands[name] = kwargs

    def register_cli_command(self, **kwargs):
        self.cli[kwargs["name"]] = kwargs

    def register_skill(self, name, path):
        self.skills[name] = path


def _item(key, title, last_names, date, item_type="journalArticle", **data):
    return {
        "key": key,
        "meta": {"creatorSummary": last_names[0] + (" et al." if len(last_names) > 2 else ""),
                 "parsedDate": date, "numChildren": data.pop("children", 0)},
        "data": {"key": key, "itemType": item_type, "title": title, "date": date,
                 "creators": [{"creatorType": "author", "firstName": "A", "lastName": n} for n in last_names],
                 "tags": [{"tag": t} for t in data.pop("tags", [])], **data},
    }


VASWANI = _item("VASW2017", "Attention Is All You Need",
                ["Vaswani", "Shazeer", "Parmar"], "2017-06-12", "preprint",
                DOI="10.48550/arXiv.1706.03762", children=3, tags=["transformers"],
                abstractNote="The dominant sequence transduction models...")
LECUN = _item("LECU2015", "Deep learning", ["LeCun", "Bengio", "Hinton"], "2015-05-28",
              DOI="10.1038/nature14539", publicationTitle="Nature")
PDF = {"key": "PDFA0001", "data": {"key": "PDFA0001", "itemType": "attachment", "parentItem": "VASW2017",
                                  "contentType": "application/pdf", "title": "Full Text PDF", "linkMode": "imported_url"}}
NOTE = {"key": "NOTE0001", "data": {"key": "NOTE0001", "itemType": "note", "note": "<p>Key idea: <b>self-attention</b></p>"}}
ANNOTS = [
    {"key": "ANNO0002", "data": {"itemType": "annotation", "annotationType": "highlight",
                                 "annotationText": "multi-head attention", "annotationPageLabel": "4",
                                 "annotationSortIndex": "00003|000200|00100"}},
    {"key": "ANNO0001", "data": {"itemType": "annotation", "annotationType": "highlight",
                                 "annotationText": "dispensing with recurrence", "annotationComment": "<p>main claim</p>",
                                 "annotationPageLabel": "1", "annotationSortIndex": "00000|000100|00050"}},
]
COLLECTIONS = [{"key": "COLL0001", "meta": {"numItems": 2}, "data": {"key": "COLL0001", "name": "Thesis"}}]
FULLTEXT = ("Abstract. We propose the Transformer, dispensing with recurrence entirely. " * 3
            + "x" * 2000 + " Scaled dot-product attention computes weights. " + "y" * 2000)


class FakeZotero:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict]] = []
        self.local_api_enabled = True
        self.saved: list[dict] = []
        self.attachments: list[tuple[dict, bytes]] = []
        self.library = [VASWANI, LECUN]
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code, body, ctype="application/json"):
                raw = body if isinstance(body, bytes) else (
                    json.dumps(body).encode() if ctype == "application/json" else str(body).encode())
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
                outer.requests.append(("GET", parsed.path, params))
                if parsed.path == "/connector/ping":
                    return self._send(200, b"<html><body>Zotero is running</body></html>", "text/html")
                if not outer.local_api_enabled:
                    return self._send(403, "Local API is not enabled", "text/plain")
                code, body, ctype = outer.route(parsed.path.removeprefix("/api/users/0"), params)
                self._send(code, body, ctype)

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                if self.path == "/connector/saveAttachment":
                    outer.attachments.append((json.loads(self.headers["X-Metadata"]), raw))
                    return self._send(201, b"", "application/json")
                payload = json.loads(raw or b"{}")
                outer.requests.append(("POST", self.path, payload))
                if self.path == "/connector/saveItems":
                    outer.saved.extend(payload["items"])
                    return self._send(201, b"", "application/json")
                if self.path == "/connector/getSelectedCollection":
                    return self._send(200, {"libraryName": "My Library", "name": "Thesis", "id": 1})
                self._send(404, "nope", "text/plain")

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def _search(self, params):
        items = list(self.library)
        q = params.get("q", "").lower()
        if q:
            def blob(i):
                d = i["data"]
                base = [d["title"], d["date"]] + [c["lastName"] for c in d["creators"]]
                if params.get("qmode") == "everything":
                    base += [d.get("DOI", ""), d.get("abstractNote", "")]
                return " ".join(base).lower()
            items = [i for i in items if q in blob(i)]
        if params.get("tag"):
            items = [i for i in items if {"tag": params["tag"]} in i["data"]["tags"]]
        return items

    def route(self, path, params):
        by_key = {i["key"]: i for i in self.library + [PDF, NOTE]}
        if path in ("/items/top", "/collections/COLL0001/items/top"):
            return 200, self._search(params), "application/json"
        if path == "/collections":
            return 200, COLLECTIONS, "application/json"
        if path == "/items" and "itemKey" in params:
            keys = params["itemKey"].split(",")
            fmt = params.get("format", "json")
            if fmt == "bibtex":
                return 200, "\n".join(f"@article{{{k.lower()}}}" for k in keys), "text/plain"
            rows = []
            for key in keys:
                if key in by_key:
                    row = dict(by_key[key])
                    title = row["data"]["title"]
                    row["bib"] = f'<div class="csl-entry">{title} ({params.get("style")}).</div>'
                    row["citation"] = f"<span>({title[:8]})</span>"
                    rows.append(row)
            return 200, rows, "application/json"
        if path == "/items/VASW2017/children":
            return 200, [PDF, NOTE], "application/json"
        if path == "/items/PDFA0001/children":
            return 200, ANNOTS, "application/json"
        if path.endswith("/children"):
            return 200, [], "application/json"
        if path == "/items/PDFA0001/fulltext":
            return 200, {"content": FULLTEXT, "indexedPages": 11, "totalPages": 11}, "application/json"
        if path.startswith("/items/") and path.count("/") == 2:
            key = path.split("/")[2]
            if key in by_key:
                return 200, by_key[key], "application/json"
            return 404, "Item not found", "text/plain"
        return 404, "Not found", "text/plain"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def fake():
    server = FakeZotero()
    try:
        yield server
    finally:
        server.stop()


CROSSREF = {"message": {
    "type": "journal-article", "title": ["Mastering the game of Go"], "DOI": "10.1038/nature16961",
    "author": [{"given": "David", "family": "Silver"}, {"given": "Aja", "family": "Huang"}],
    "container-title": ["Nature"], "issued": {"date-parts": [[2016, 1, 27]]},
    "volume": "529", "page": "484-489", "abstract": "<jats:p>The game of Go...</jats:p>",
}}
ARXIV = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Denoising Diffusion
 Probabilistic Models</title><published>2020-06-19T00:00:00Z</published>
<summary>We present high quality image synthesis.</summary>
<author><name>Jonathan Ho</name></author><author><name>Ajay Jain</name></author></entry></feed>"""


def fake_fetch(url: str) -> bytes:
    if url.startswith(resolve_mod.CROSSREF_URL):
        if "nature16961" in url:
            return json.dumps(CROSSREF).encode()
        raise PLUGIN.tools.ZoteroError(f"No record found at {url}.")
    if url.startswith(resolve_mod.ARXIV_URL):
        return ARXIV
    if url == "https://arxiv.org/pdf/2006.11239":
        return b"%PDF-1.7 fake pdf body"
    raise AssertionError(f"unexpected fetch {url}")


def _tools(fake_server, **config):
    ctx = FakeCtx({"zotero_url": fake_server.url, "request_timeout": 3.0, **config})
    return PLUGIN.ZoteroTools(ctx, fetch=fake_fetch)


def _call(tools, name, **args):
    return json.loads(getattr(tools, name)(args))


# ---------- registration ----------


def test_register_exposes_tools_command_cli_and_skill():
    ctx = FakeCtx()
    PLUGIN.register(ctx)
    assert set(ctx.tools) == {
        "zotero_status", "zotero_search", "zotero_item", "zotero_fulltext",
        "zotero_cite", "zotero_add", "zotero_verify",
    }
    for tool in ctx.tools.values():
        assert tool["toolset"] == "zotero"
        assert tool["schema"]["parameters"]["type"] == "object"
        assert callable(tool["handler"])
    assert "zotero" in ctx.commands and "zotero" in ctx.cli
    assert ctx.skills["research"].is_file()


def test_manifest_lists_exactly_the_registered_tools():
    manifest = (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")
    ctx = FakeCtx()
    PLUGIN.register(ctx)
    for name in ctx.tools:
        assert f"  - {name}\n" in manifest


# ---------- status / errors ----------


def test_status_lists_collections_and_recent_items(fake):
    result = _call(_tools(fake), "status")
    assert result["connected"] is True
    assert result["collections"] == [{"key": "COLL0001", "name": "Thesis", "items": 2, "parent": None}]
    assert result["recently_added"][0]["title"] == "Attention Is All You Need"


def test_disabled_local_api_explains_the_setting(fake):
    fake.local_api_enabled = False
    result = _call(_tools(fake), "search", query="attention")
    assert result["error"] == "Zotero's local API is turned off."
    assert "Allow other applications" in result["hint"]


def test_zotero_not_running_gives_an_actionable_error():
    tools = PLUGIN.ZoteroTools(FakeCtx({"zotero_url": "http://127.0.0.1:9", "request_timeout": 1}))
    result = _call(tools, "status")
    assert "Could not reach Zotero" in result["error"]
    assert "Open the Zotero desktop app" in result["hint"]


def test_bad_item_key_is_rejected_before_any_request(fake):
    result = _call(_tools(fake), "item", key="../../etc")
    assert "not a Zotero item key" in result["error"]
    assert not [r for r in fake.requests if "etc" in r[1]]


# ---------- search ----------


def test_search_returns_compact_rows(fake):
    result = _call(_tools(fake), "search", query="vaswani")
    assert result["count"] == 1
    row = result["items"][0]
    assert row == {
        "key": "VASW2017", "title": "Attention Is All You Need", "authors": "Vaswani et al.",
        "year": "2017", "type": "preprint", "doi": "10.48550/arXiv.1706.03762",
        "tags": ["transformers"], "children": 3,
    }
    assert fake.requests[-1][2]["qmode"] == "titleCreatorYear"


def test_search_everything_and_collection_by_name(fake):
    result = _call(_tools(fake), "search", query="nature14539", everything=True, collection="thesis")
    assert [i["key"] for i in result["items"]] == ["LECU2015"]
    assert fake.requests[-1][1] == "/api/users/0/collections/COLL0001/items/top"
    assert fake.requests[-1][2]["qmode"] == "everything"


def test_search_unknown_collection_lists_real_ones(fake):
    result = _call(_tools(fake), "search", query="x", collection="Nope")
    assert "No Zotero collection" in result["error"]
    assert "Thesis" in result["hint"]


def test_empty_search_suggests_next_steps(fake):
    result = _call(_tools(fake), "search", query="quantum gravity")
    assert result["count"] == 0
    assert "everything=true" in result["message"]


# ---------- item ----------


def test_item_includes_notes_attachments_and_sorted_highlights(fake):
    item = _call(_tools(fake), "item", key="vasw2017")["item"]
    assert item["abstractNote"].startswith("The dominant")
    assert item["notes"] == [{"key": "NOTE0001", "text": "Key idea: self-attention"}]
    assert item["attachments"][0]["key"] == "PDFA0001"
    assert [a["text"] for a in item["annotations"]] == ["dispensing with recurrence", "multi-head attention"]
    assert item["annotations"][0]["comment"] == "main claim"
    assert item["annotations"][0]["page"] == "1"
    assert all("_sort" not in a for a in item["annotations"])


def test_missing_item_is_a_clean_error(fake):
    result = _call(_tools(fake), "item", key="ZZZZ9999")
    assert "Zotero has nothing" in result["error"]


# ---------- fulltext ----------


def test_fulltext_find_returns_only_matching_passages(fake):
    result = _call(_tools(fake), "fulltext", key="VASW2017", find=["dot-product", "absent term"])
    assert len(result["passages"]) == 1
    assert "Scaled dot-product attention" in result["passages"][0]["text"]
    assert len(result["passages"][0]["text"]) < 800
    assert result["not_found"] == ["absent term"]


def test_fulltext_truncates_to_max_chars(fake):
    result = _call(_tools(fake), "fulltext", key="VASW2017", max_chars=500)
    assert result["truncated"] is True
    assert len(result["text"]) == 500
    assert result["total_chars"] == len(FULLTEXT)


def test_fulltext_falls_back_to_zotero_cache_file(fake, tmp_path):
    fake.local_api_enabled = True
    cache = tmp_path / "storage" / "PDFA0001"
    cache.mkdir(parents=True)
    (cache / ".zotero-ft-cache").write_text("cached body text", encoding="utf-8")
    original = fake.route

    def no_fulltext(path, params):
        if path.endswith("/fulltext"):
            return 501, "Not implemented", "text/plain"
        return original(path, params)

    fake.route = no_fulltext
    result = _call(_tools(fake, data_dir=str(tmp_path)), "fulltext", key="VASW2017")
    assert result["text"] == "cached body text"


def test_fulltext_without_pdf_says_how_to_fix(fake):
    result = _call(_tools(fake), "fulltext", key="LECU2015")
    assert "no indexed text" in result["error"]
    assert "Reindex" in result["hint"]


# ---------- cite ----------


def test_cite_bibliography_uses_zotero_style_in_requested_order(fake):
    result = _call(_tools(fake, citation_style="ieee"), "cite", keys=["LECU2015", "VASW2017"])
    assert result["style"] == "ieee"
    assert [e["key"] for e in result["entries"]] == ["LECU2015", "VASW2017"]
    assert result["text"].splitlines() == ["Deep learning (ieee).", "Attention Is All You Need (ieee)."]
    assert fake.requests[-1][2]["include"] == "data,bib"


def test_cite_reports_missing_keys_and_exports_bibtex(fake):
    result = _call(_tools(fake), "cite", keys=["VASW2017", "ZZZZ9999"], style="apa")
    assert result["missing_keys"] == ["ZZZZ9999"]
    bib = _call(_tools(fake), "cite", keys=["VASW2017"], output="bibtex")
    assert bib["text"] == "@article{vasw2017}"


# ---------- add ----------


def test_add_resolves_saves_and_skips_existing(fake):
    result = _call(_tools(fake), "add",
                   identifiers=["https://doi.org/10.1038/nature16961", "arXiv:2006.11239", "1706.03762"],
                   tags=["to-read"])
    assert result["saved"] == 2
    assert result["already_in_library"] == 1
    assert result["saved_to"] == "Thesis"
    go, ddpm = fake.saved
    assert go["itemType"] == "journalArticle"
    assert go["publicationTitle"] == "Nature"
    assert go["date"] == "2016-01-27"
    assert go["creators"][0] == {"creatorType": "author", "firstName": "David", "lastName": "Silver"}
    assert go["abstractNote"] == "The game of Go..."
    assert go["tags"] == [{"tag": "to-read"}]
    assert ddpm["itemType"] == "preprint"
    assert ddpm["title"] == "Denoising Diffusion Probabilistic Models"
    assert "attachments" not in ddpm  # uploaded separately, not via saveItems
    assert result["pdfs_attached"] == 1
    (meta, body), = fake.attachments
    assert meta["parentItemID"] == ddpm["id"]
    assert meta["sessionID"] == fake.requests[-2][2]["sessionID"]
    assert body.startswith(b"%PDF")
    assert result["results"][1]["pdf"] == "attached"
    assert "pdf" not in result["results"][0]  # Crossref items carry no PDF
    assert result["results"][2]["key"] == "VASW2017"


def test_add_keeps_the_item_when_the_pdf_download_is_not_a_pdf(fake):
    tools = PLUGIN.ZoteroTools(
        FakeCtx({"zotero_url": fake.url, "request_timeout": 3.0}),
        fetch=lambda url: b"<html>captcha</html>" if "/pdf/" in url else fake_fetch(url),
    )
    result = _call(tools, "add", identifiers=["2006.11239"])
    assert result["saved"] == 1 and result["pdfs_attached"] == 0
    assert result["results"][0]["pdf"].startswith("not attached")
    assert fake.attachments == []


def test_add_can_skip_pdfs(fake):
    result = _call(_tools(fake), "add", identifiers=["2006.11239"], attach_pdf=False)
    assert result["saved"] == 1 and result["pdfs_attached"] == 0
    assert fake.attachments == []


def test_add_reports_bad_identifiers_without_saving(fake):
    result = _call(_tools(fake), "add", identifiers=["not an id", "10.9999/missing"])
    assert result["saved"] == 0 and result["failed"] == 2
    assert fake.saved == []
    assert not [r for r in fake.requests if r[1] == "/connector/saveItems"]


def test_add_rejects_oversized_batches(fake):
    result = _call(_tools(fake), "add", identifiers=[f"10.1/{i}" for i in range(21)])
    assert "limit is 20" in result["error"]


@pytest.mark.parametrize("raw, expected", [
    ("10.1038/nature14539", ("doi", "10.1038/nature14539")),
    ("https://doi.org/10.1145/3292500.3330701.", ("doi", "10.1145/3292500.3330701")),
    ("https://arxiv.org/abs/1706.03762v5", ("arxiv", "1706.03762")),
    ("10.48550/arXiv.2005.14165", ("arxiv", "2005.14165")),
    ("hep-th/9711200", ("arxiv", "hep-th/9711200")),
])
def test_identifier_parsing(raw, expected):
    assert resolve_mod.parse_identifier(raw) == expected


# ---------- verify ----------


def test_extract_citations_finds_parenthetical_narrative_and_doi():
    text = ("Transformers replaced RNNs (Vaswani et al., 2017; LeCun, Bengio & Hinton, 2015). "
            "Smith and Lee (2020) disagree. See https://doi.org/10.1038/nature14539.")
    found = library.extract_citations(text)
    pairs = [(c.get("author"), c.get("year")) for c in found if c["kind"] == "author_year"]
    assert pairs == [("Vaswani", "2017"), ("LeCun", "2015"), ("Smith", "2020")]
    assert [c["doi"] for c in found if c["kind"] == "doi"] == ["10.1038/nature14539"]


@pytest.mark.parametrize("size", [290, 2048])
def test_adversarial_parenthetical_returns_quickly(size):
    # "and" between capitalised letters used to let separators overlap names: exponential backtracking.
    body = ("A" + "andA" * size)[: size - len(" x 2020")]
    text = f"Claim ({body} x 2020)."
    started = time.perf_counter()
    found = library.extract_citations(text)
    assert time.perf_counter() - started < 0.1
    assert [c for c in found if c["kind"] == "author_year"] == []


def test_verify_flags_citations_not_in_the_library(fake):
    text = "Attention works (Vaswani et al., 2017). Deep nets (LeCun et al., 2016). Hinton (2006) too."
    result = _call(_tools(fake), "verify", text=text)
    by_author = {c["author"]: c for c in result["checks"]}
    assert by_author["Vaswani"]["matches"][0]["key"] == "VASW2017"
    assert by_author["LeCun"]["matches"] == []  # right author, wrong year
    assert by_author["Hinton"]["matches"] == []
    assert result["matched"] == 1 and result["unmatched"] == 2
    assert "not in the library" in result["message"]


def test_verify_matches_dois(fake):
    result = _call(_tools(fake), "verify", text="as shown in doi:10.1038/nature14539")
    assert result["matched"] == 1


# ---------- slash + CLI ----------


def test_slash_status_search_cite_and_help(fake):
    tools = _tools(fake)
    assert "Zotero is connected" in tools.slash("")
    assert "[VASW2017] Vaswani et al. (2017)" in tools.slash("search attention")
    assert tools.slash("cite VASW2017") == "Attention Is All You Need (apa)."
    assert "Usage: /zotero" in tools.slash("help")
    assert "Unknown /zotero subcommand" in tools.slash("frobnicate")


def test_slash_reports_errors_as_text(fake):
    fake.local_api_enabled = False
    reply = _tools(fake).slash("search attention")
    assert reply.startswith("Zotero error: Zotero's local API is turned off.")


def test_cli_parses_and_runs(fake, capsys):
    tools = _tools(fake)
    parser = argparse.ArgumentParser()
    PLUGIN.tools._setup_cli(parser)
    assert tools.cli(parser.parse_args(["cite", "VASW2017", "--format", "bibtex"])) == 0
    assert "@article{vasw2017}" in capsys.readouterr().out
    assert tools.cli(parser.parse_args(["search", "deep"])) == 0
    assert "LECU2015" in capsys.readouterr().out
    fake.local_api_enabled = False
    assert tools.cli(parser.parse_args(["status"])) == 1


def test_passages_match_across_pdf_line_breaks_and_hyphenation():
    text = "we use a masked\nlanguage model and next sen-\ntence prediction."
    found = library.passages(text, ["masked language model", "next sentence  prediction"], radius=1)
    assert [p["terms"] for p in found] == [["masked language model"], ["next sentence prediction"]]
