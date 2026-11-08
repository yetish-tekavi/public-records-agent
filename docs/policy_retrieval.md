# Policy Retrieval (Milestone 2)

The retrieval component finds verbatim passages in official New Jersey policy documents.
It returns **evidence only**: no summaries, recommendations, or legal conclusions.
Later stages decide what to recommend, and a human reviewer always approves.

## Sources

Listed in [`data/policy_documents/manifest.json`](../data/policy_documents/manifest.json) with
title, publisher, source URL, effective date (as stated by the source), and retrieval date.

| doc_id | Document | Effective date | Format |
|---|---|---|---|
| `opra_statute` | Open Public Records Act, N.J.S.A. 47:1A-1 et seq. (GRC readable version) | as of September 3, 2024 | PDF, 37 pp. |
| `grc_exemptions` | OPRA Exemptions (GRC list) | updated September 3, 2024 | PDF, 10 pp. |
| `grc_redacting` | Redacting Government Records (GRC web page) | not stated | HTML |
| `grc_handbook` | Handbook for Records Custodians, 8th edition | January 2025 | PDF, 39 pp. |
| `ag_bwc_directive` | AG Directive 2022-1, Body Worn Camera Policy | January 19, 2022 | PDF, 36 pp. |

## Pipeline

```
manifest.json ──► extract.py ──► chunker.py ──► keyword.py  (BM25)        ─┐
                  PDF: pypdf      section labels   semantic.py (ChromaDB +   ├─► retriever.py ──► PolicyRetrievalResult
                  HTML: div.mainText  + page range   all-MiniLM-L6-v2)      ─┘    thresholds        SUFFICIENT + passages
                  strip headers/footers                                                            or INSUFFICIENT + none
```

1. **Extract.** `pypdf` reads each page. Whitespace is collapsed and the running headers and footers
   listed in the manifest are removed. For the HTML page, only `div.mainText` is read, so navigation
   and footer text are excluded.
2. **Chunk.** Lines are grouped into chunks of at most 900 characters. A chunk never crosses a
   section boundary, and consecutive chunks within a section overlap by one line. Each document
   style has its own heading detector:
   - statute: `C.47:1A-1.1 Definitions.`, plus lettered subsections such as `N.J.S.A. 47:1A-5(a)`. A subsection
     may follow a margin note on the same line, and a letter is accepted only if it is next in sequence.
   - exemptions list: `N.J.S.A. 47:1A-1.1` group, then numbered items such as `5) Criminal investigatory records`
   - handbook: `SECTION 2 – ...` plus question headings
   - directive: `11 Public Disclosure of BWC Recordings`
   - HTML: bold-only paragraphs

   Each chunk carries the document title, source URL, effective date, section label and page range.
   Chunk text is never rewritten, and a test checks this.
3. **Retrieve.**
   - `keyword`: BM25 over the chunk text, with the section label counted twice.
   - `semantic`: cosine similarity in a persistent local ChromaDB collection (`outputs/policy_index/`).
     It is rebuilt automatically whenever the chunk text changes.
   - `hybrid` (the default): reciprocal-rank fusion of the two.
4. **Gate on evidence.** A passage is returned only if it clears an explicit threshold (see below).
   If nothing does, the result is `INSUFFICIENT` with no passages, and the message says
   *"Human review required."*

## Evidence thresholds

Calibrated on the bundled corpus against the known and missing questions in the tests
(`EvidenceThresholds` in `app/policies/retriever.py`).

| Signal | Rule |
|---|---|
| Keyword | BM25 score ≥ 6.0, **and** at least 60% of distinct query terms in the passage, **and** at least 2 matched terms, **and** no query term that appears nowhere in the corpus |
| Semantic | cosine similarity ≥ 0.55 |
| Hybrid | a passage qualifies if it passes either rule |
| Jurisdiction | a query that names another US state or a foreign privacy regime (for example "California" or "GDPR") is refused outright |

## Separation of evidence and recommendations

- `PolicyRetrievalResult` holds the query, method, status, verbatim passages with their scores,
  and a fixed status message. It has no field for a conclusion.
- `RetrievedPassage.to_policy_reference()` builds a citation only from document metadata (title
  and section label) and the verbatim chunk text, never from model output.
- `RedactionRecommendation` (Milestone 1) rejects `REDACT` without a policy reference, so an
  `INSUFFICIENT` retrieval can only lead to `HUMAN_REVIEW_REQUIRED`.
- Retrieved text is treated as data. Nothing in a passage is executed or followed as an instruction.

## Limitations

1. **Retrieval is not interpretation.** A passage that mentions telephone numbers is evidence to
   review, not a determination that a particular number must be redacted. Exceptions, case law,
   and context (for example, whether a number is a public official's office line) are not modelled.
2. **The corpus is small and static.** There are five documents and no GRC decisions, court opinions
   or other statutes, even though OPRA's exemptions defer to "any other statute". A correct
   INSUFFICIENT result can therefore mean "not in these five documents", not "no law applies".
   Documents must be re-downloaded by hand when updated.
3. **The effective dates are document dates.** They reflect each document's stated date, not
   whether the law has changed since. The AG directive (January 2022) may have been superseded.
4. **PDF extraction artifacts.** `pypdf` introduces split words (`e -mail`, `t he`), and the statute's
   left-margin notes ("Personal information (including Daniel's Law information)") are mixed into the body text.
   Excerpts are verbatim *extracted* text, so a reviewer should confirm wording against the source PDF.
5. **Section labels are heuristic.** A few chunks have no section (cover pages, tables of contents),
   and long headings are truncated with "…". The page range is the reliable locator.
6. **Keyword limits.** Vocabulary mismatch is only partly handled by a small synonym map
   (`body worn camera`↔`BWC`, `phone`↔`telephone`, `SSN`, `DOB`). The stemmer is deliberately crude.
   The unknown-term rule can reject valid questions that use words absent from the corpus
   (for example "Is 911 call audio a public record?" returns INSUFFICIENT because "911" never appears).
7. **Semantic limits.** all-MiniLM-L6-v2 is a small general-purpose model. It cannot tell jurisdictions
   apart (hence the explicit jurisdiction rule), and it scores some off-topic questions near the
   threshold (for example 0.53 for a California hospital-records question). Thresholds were calibrated
   on about 30 questions, which is not a statistically robust evaluation.
8. **The jurisdiction rule is a word list.** It can misfire on names (for example "Washington Street")
   and misses jurisdictions it doesn't list.
9. **Hybrid fusion favours agreement.** A passage ranked first by one method but poorly by the other
   can drop out of the top results. Use `--method keyword` or a larger `-k` to inspect.
