---
name: zotero-research
description: "Answer research questions and write cited text grounded in the user's own Zotero library, with zero invented references."
version: 1.0.0
author: suhas-svg
license: MIT
metadata:
  hermes:
    tags: [zotero, research, citations, literature-review]
---

# Zotero research workflow

Use this skill when the user asks about papers, sources or "my library", wants a
literature review, summary, related-work section or annotated bibliography, or
asks you to cite, check or add references.

## Ground rules

- **Every reference comes from Zotero.** Format citations only with `zotero_cite`.
  Never type a reference, DOI, page number or quote from memory.
- **Quote only what a tool returned:** highlights from `zotero_item`, passages
  from `zotero_fulltext`. Give the page label when the tool supplied one.
- **Say where the library is thin.** If the library has nothing on a point,
  say so and offer to find and add papers instead of filling the gap yourself.

## Answer a question from the library

1. `zotero_search` with the topic words. If few hits, retry with
   `everything: true` to match abstracts, notes and PDF text.
2. For the 2–6 most relevant items, call `zotero_item`. The user's own
   highlights and comments show what they found important; lead with those.
3. Need a specific claim, method or number? `zotero_fulltext` with `find`
   set to the key terms returns just those passages.
4. Write the answer with in-text citations, then call `zotero_cite` with the
   keys you used (`output: citation` for in-text, `bibliography` for the list).
5. Before handing over, run `zotero_verify` on your draft. Fix or remove any
   citation it reports as unmatched.

## Literature review / related work

Group items by theme or method, not one paragraph per paper. For each theme:
what the papers agree on, where they disagree, and what is missing. End with a
bibliography from `zotero_cite` in the user's requested style (ask if unsure;
the plugin default is used otherwise). Offer BibTeX via `output: bibtex` for LaTeX users.

## Check someone's draft

Run `zotero_verify` on the text. Report: citations matched (with item keys),
citations not in the library, and any DOI that does not resolve to a saved item.
Do not judge whether a claim is supported until you have read the matched item.

## Add papers

1. Collect DOIs or arXiv ids (from the user, or from a paper's reference list).
2. Show the list and ask before saving. Papers go into the collection that is
   currently selected in Zotero, so tell the user to select one first if needed.
3. `zotero_add` skips papers already in the library and attaches arXiv PDFs.
   Report what was saved, skipped and failed.
