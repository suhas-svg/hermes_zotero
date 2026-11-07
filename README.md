# Zotero Research for Hermes

Ask Hermes about **your own** papers and get answers grounded in your Zotero
library: your PDF highlights, the paper's full text, and citations formatted by
Zotero's own citation engine. No more invented references.

```
You: What does BERT say about its pre-training objectives? Cite it in APA.
Hermes: BERT trains with a "masked language model" objective, which randomly
        masks input tokens and predicts them from both directions, plus a
        "next sentence prediction" task (Devlin et al., 2018) ...
        Devlin, J., Chang, M.-W., Lee, K., & Toutanova, K. (2018). BERT: ...

You: Check the citations in my draft.
Hermes: 3 of 4 citations match items in your library. (Hochreiter &
        Schmidhuber, 1997) is not in Zotero. Want me to add it by DOI?

You: Add arXiv 2006.11239 to my library.
Hermes: Saved "Denoising Diffusion Probabilistic Models" (Ho et al., 2020)
        with its PDF attached.
```

## What it adds

| Surface | What you get |
| --- | --- |
| Tools | `zotero_status`, `zotero_search`, `zotero_item`, `zotero_fulltext`, `zotero_cite`, `zotero_add`, `zotero_verify` |
| Slash command | `/zotero status`, `/zotero search WORDS`, `/zotero cite KEY...` |
| CLI | `hermes zotero status`, `hermes zotero search WORDS`, `hermes zotero cite KEY... [--style ieee] [--format bibtex]` |
| Skill | `zotero:research`: grounded Q&A, literature reviews, draft checking, adding papers |

| Tool | Does |
| --- | --- |
| `zotero_search` | Title/creator/year search, or `everything` to include abstracts, notes and indexed PDF text; filter by tag, collection or item type. |
| `zotero_item` | Full metadata, abstract, your notes, attachments, and **every PDF highlight and comment with its page label**. |
| `zotero_fulltext` | The PDF's indexed text. With `find`, only the passages around those phrases (robust to PDF line breaks and hyphenation). |
| `zotero_cite` | Bibliography or in-text citations in any CSL style installed in Zotero (APA, IEEE, Chicago, Nature, ...), or BibTeX / BibLaTeX / RIS / CSL-JSON export. |
| `zotero_add` | Adds papers by DOI or arXiv id via Crossref / arXiv metadata, skips ones already in the library, and attaches arXiv PDFs (which Zotero then indexes). |
| `zotero_verify` | Finds DOIs and author–year citations in a draft, such as `(Vaswani et al., 2017; LeCun, 2015)` or `Smith and Lee (2020)`, and reports which match a library item and which don't. |

A prompt alone can't do this. The model reads your actual annotations and
PDFs, Zotero formats the references, and every citation can be checked against
what you really have.

## Requirements

- Hermes Agent 0.22.5 or newer.
- [Zotero 7 or newer](https://www.zotero.org/download/) desktop, running.
- In Zotero: **Settings → Advanced → "Allow other applications on this computer
  to communicate with Zotero"** (this turns on Zotero's local API).

No Python dependencies; the plugin uses the standard library only.

## Install

```bash
hermes plugins install suhas-svg/hermes_zotero --enable
```

Once the plugin is in the curated catalog:

```bash
hermes plugins install zotero
```

Verify it:

```bash
hermes zotero status
```

If Zotero isn't running or the local API is switched off, the command tells you
exactly what to change instead of printing a traceback.

## Use it

Ask Hermes in ordinary language:

- "What do the papers in my library say about diffusion models?"
- "Summarise my highlights from the BERT paper."
- "Find where the Transformer paper describes positional encoding."
- "Write a related-work paragraph from my Thesis collection, IEEE style."
- "Check the citations in this paragraph against my library."
- "Add these DOIs to Zotero: 10.1038/nature14539, 10.1038/nature16961."
- "Give me BibTeX for everything tagged transformers."

## Configuration

Open **Settings → Plugins → Zotero** in the Hermes desktop app, or edit
`plugins.entries.zotero.settings` in `config.yaml`.

| Key | Default | Purpose |
| --- | --- | --- |
| `zotero_url` | `http://127.0.0.1:23119` | Zotero desktop's local HTTP server. |
| `citation_style` | `apa` | Default CSL style for `zotero_cite` (must be installed in Zotero). |
| `data_dir` | empty (`~/Zotero`) | Zotero data directory; used to read the full-text cache if the local API can't serve it. |
| `request_timeout` | `15.0` | Seconds to wait for Zotero (clamped to 1–120); metadata and PDF downloads allow up to 60. |

## Safety and disclosures

- **Reads your library locally.** Search, item, full-text and citation calls go
  only to Zotero on `127.0.0.1:23119`. Zotero's local API is read-only.
- **`zotero_add` is the only write and the only outbound call.** It runs only
  when the tool is called. It fetches public metadata from `api.crossref.org`
  (DOIs) or `export.arxiv.org` (arXiv ids), downloads arXiv PDFs from
  `arxiv.org`, and saves the new items through Zotero's connector endpoint
  (the same one the browser extension uses), into the collection currently
  selected in Zotero. Only the identifier you pass is sent to those services.
- **Duplicates are skipped.** Before saving, it searches your library for the
  DOI / arXiv id and reports existing items instead of re-adding them.
- **No telemetry.** No analytics, no update checker, no background jobs.
- **No credentials.** No API keys are needed; it does not read `.env`,
  `auth.json` or any Zotero account data.
- **Reads files only inside the Zotero data directory**: the
  `storage/<key>/.zotero-ft-cache` text file Zotero writes when it indexes a PDF.

## Development

```bash
python -m pytest -q
hermes plugins doctor .
hermes plugins validate . --install-deps
```

The tests run a fake Zotero server on `127.0.0.1:0` (local API plus connector)
and inject the metadata fetcher, so CI needs neither Zotero nor network access.

## License

MIT
