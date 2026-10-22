# CT-200 Approach Document

## 1. Goal

Build a CT-200-specific backend that converts the supplied PDF manuals into a browsable, versioned hierarchy, keeps exact source traceability, generates structured QA test cases, and exposes whether previous generations are stale after re-ingestion.

## 2. Extraction approach

I use PyMuPDF as the primary parser because the supplied CT-200 PDFs contain a usable text layer. The parser reads blocks in document order and uses section numbering, font size and boldness to identify headings. A hierarchy stack determines parent/child relationships. Tables are extracted with PyMuPDF's table detection and stored under the surrounding node.

If the embedded text layer does not expose headings, the parser attempts an OCR fallback using Tesseract/pytesseract. It also emits warnings when pages contain embedded images so that visual-only content is not silently treated as fully parsed.

Each node stores:

- heading
- hierarchy level
- body text
- parent/children
- page start
- position
- logical key
- SHA-256 content hash

The implementation is intentionally specific to this CT-200 document rather than a generic PDF parser, which is explicitly out of scope in the assignment.

## 3. Structural irregularities and how they are handled

The supplied PDFs were inspected directly before implementing the parser. Important cases include:

- wrapped title and page text
- deeply numbered section `2.1.1.1`
- non-perfect section numbering/order
- duplicate-looking headings
- tables embedded in normal page text flow
- list-like text that must remain body content
- page breaks between headings and their body
- embedded images

Handling:

- Duplicate headings receive sibling occurrence numbers in their logical key, so they remain separate nodes.
- Numbered headings are classified from their numbering pattern and typography rather than from a hardcoded list of section names.
- Unnumbered short headings can be detected from typography.
- Page breaks do not reset the hierarchy stack.
- Body text is appended to the current node instead of being promoted to a section unless it passes the heading rules.
- Tables are extracted once and attached to the current section.
- If a scanned/image-only document exposes no headings, OCR is attempted. Unsupported visual content is reported through warnings instead of being silently discarded.

## 4. Validation methodology

Validation was performed in three layers.

### Unit tests

The test suite covers more than the required three irregularities:

1. duplicate headings remain distinct
2. nested headings receive correct parents
3. long body text is not promoted to a fake section
4. exact logical-key version matching
5. conservative fuzzy matching
6. malformed LLM output is rejected
7. real CT-200 v1/v2 extraction is checked

### Real CT-200 PDFs

The supplied v1 PDF currently parses to 27 nodes and v2 to 28 nodes. v2 adds `5.3 Data Export`. The Battery Life node is matched between versions and its content hash changes.

### End-to-end API validation

The demo script exercises the required flow:

1. ingest v1
2. search for a real node
3. create a version-pinned selection
4. generate three structured QA cases
5. verify the generation is fresh
6. ingest v2
7. compare the original node against v2
8. retrieve the generation and verify `stale=true`

## 5. Versioning strategy

The primary matching strategy is a normalized heading path plus sibling occurrence. It is simple, deterministic and easy to explain.

During a new ingestion:

1. exact logical keys are matched first
2. unmatched nodes may use a conservative fuzzy-heading fallback
3. fuzzy matching is allowed only when hierarchy level and parent path agree
4. the best score must be at least 0.88 and have a clear margin over the next candidate
5. old/new node mappings are persisted in `node_matches`
6. a matched node can still be marked changed because its content hash is compared independently

### Known failure modes

This strategy can fail when:

- a section is moved to a completely different parent
- a heading is rewritten substantially
- several similar headings make the match ambiguous
- identical sibling headings are inserted or removed in a way that changes occurrence positions

In those cases the system prefers an explicit unmatched result over silently claiming continuity.

## 6. Browse and comparison

The API supports:

- top-level sections by version, defaulting to latest
- individual nodes with recursive children and full text
- heading/body search
- node comparison across versions

Comparison uses logical identity plus content hashes. When the body changes, a bounded unified diff is returned. If the node disappears or cannot be matched, the API reports `removed_or_unmatched`.

## 7. Version-pinned selections

A selection stores the document ID plus each selected node's:

- node ID
- version ID
- exact source content hash

Therefore re-ingesting v2 does not rewrite a selection created against v1. The selection continues to resolve to the original v1 nodes.

## 8. LLM generation and validation

The generation API reconstructs the exact selected source text and sends it to the configured LLM provider.

The prompt requires 3–5 concrete, executable QA test cases and forbids invented requirements. Every case contains:

- title
- preconditions
- steps
- expected result
- source node IDs

The provider response is parsed as JSON and validated with Pydantic. If the response is malformed or incomplete, one correction request is sent. If validation still fails, the API returns an error and does not store the invalid output.

The primary runtime provider is Google Gemini 3.1 Flash-Lite through Gemini's OpenAI-compatible endpoint. A deterministic mock provider remains only as an explicit automated-test mode. The Gemini response is requested as JSON, then independently validated with Pydantic; malformed or incomplete output triggers one correction retry, and invalid final output is not stored.

## 9. Generated-output store

The assignment permits MongoDB, Atlas free tier, or a well-justified JSON store. This implementation uses a small JSON document store to keep the project easy to run locally without an additional service.

SQLite stores relational metadata such as the generation record. The JSON store contains:

- generation/selection linkage
- source fingerprint
- exact source node snapshots used for generation
- generated test cases

This preserves the generation's source context even after the document receives another version.

## 10. Duplicate-generation policy

If the same selection is submitted again and its source fingerprint is unchanged, the existing generation is returned instead of creating duplicate output. A caller can explicitly pass `force=true` to request another generation.

This makes normal generation idempotent while still allowing deliberate regeneration.

## 11. Staleness / impact detection

At retrieval time, every selected source node is compared with the latest version's node having the same logical identity.

A generation is stale if:

- a selected source node no longer exists in the latest version, or
- its current content hash differs from the hash stored with the selection.

This is intentionally conservative. The system does not claim to understand clinical significance. A one-word wording change and a changed pressure threshold both produce `stale=true`. The compare API provides a lightweight diff so a reviewer can judge the actual impact.

## 12. Decision log

### 1. What is most likely to silently give wrong results?

Hierarchy reconstruction and version matching. A parser can return a clean-looking tree while attaching a subsection to the wrong parent. I catch this with hierarchy unit tests, logical-key checks, persisted node matches, API comparison and manual inspection of the supplied CT-200 hierarchy.

### 2. Where did I choose simplicity over correctness?

Staleness uses exact content hashes instead of semantic or clinical impact analysis. This is safe for traceability but conservative. In production, harmless wording changes would create false stale flags; a domain-aware semantic diff would be needed to reduce that noise.

### 3. One input I did not fully handle

A complex scanned table with no reliable text structure is not guaranteed to be reconstructed as a perfect table grid. The parser attempts OCR and emits warnings when visual content needs review rather than silently claiming complete extraction.

## 13. What I would do with more time

- add stronger figure/caption association and table regression tests
- store match confidence and reason as first-class versioning metadata
- classify numeric/threshold changes separately from wording changes
- add a MongoDB adapter behind the existing generated-output interface
- add golden extraction snapshots for more visual regression coverage
