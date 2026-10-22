# Tri9T AI — CT-200 Traceable QA Backend

A backend implementation of the Tri9T AI engineering assignment. The service parses the supplied CT-200 PDFs into a hierarchical tree, versions re-ingested documents, preserves exact source traceability, generates structured QA test cases, and reports stale generations after document changes.

## Stack

- FastAPI + Pydantic
- SQLAlchemy + SQLite for documents, versions, nodes, matches, and selections
- JSON document store for generated LLM output (allowed by the assignment as a lightweight NoSQL alternative)
- PyMuPDF for PDF text/layout/table extraction
- Optional Tesseract OCR fallback for scanned/image-only PDFs
- pytest + pytest-asyncio

The implementation is intentionally CT-200-specific. The assignment explicitly says a generic PDF parser is out of scope.

## Project layout

```text
app/                 API, parser, versioning, LLM and persistence code
data/                supplied CT-200 v1/v2 PDFs
docs/APPROACH.md     design decisions, limitations and decision log
scripts/demo_flow.py deterministic end-to-end versioning/staleness demo
tests/               parser, versioning and LLM validation tests
.env.example         configuration template
pytest.ini           pytest configuration
requirements.txt     pinned dependencies
```

## Setup

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

### macOS/Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Run the API

```bash
uvicorn app.main:app --reload
```

Swagger UI:

```text
http://127.0.0.1:8000/docs
```

Health check:

```text
GET /health
```

## Configuration

The primary LLM provider is **Google Gemini 3.1 Flash-Lite**. The integration uses Google's OpenAI-compatible Gemini endpoint, so no additional SDK is required. Gemini 3.1 Flash-Lite supports structured outputs.

```env
DATABASE_URL=sqlite:///./ct200.db
LLM_PROVIDER=gemini
LLM_API_KEY=your_gemini_api_key
LLM_MODEL=gemini-3.1-flash-lite
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
GENERATED_STORE_PATH=./generated_outputs.json
```

Get a Gemini API key from Google AI Studio, put it in `.env`, and restart the API. The generation endpoint sends the selected, version-pinned source text to Gemini and requests JSON output. The application then validates the response with Pydantic; invalid output triggers one correction/retry and is never stored if validation still fails.

For automated tests, `LLM_PROVIDER=mock` remains available as an explicit test-only mode. When `LLM_PROVIDER=gemini`, a missing API key is treated as a configuration error rather than silently falling back to the mock.

## Tests

```bash
pytest -q
```

The current test suite covers:

- duplicate headings remain distinct
- nested parent/child hierarchy
- body text is not incorrectly promoted to a section
- logical-key version matching
- conservative fuzzy matching
- malformed/incomplete LLM output validation
- real CT-200 v1/v2 hierarchy and version differences

## End-to-end demo

Start the server first:

```bash
uvicorn app.main:app --reload
```

Then, in another terminal:

```bash
python scripts/demo_flow.py
```

The script uses a separate document name (`ct200_demo`) and performs the complete flow automatically:

```text
v1 PDF ingestion
      ↓
find Battery Life node
      ↓
create version-pinned selection
      ↓
generate 3 QA cases
      ↓
verify generation is initially fresh
      ↓
ingest v2 PDF
      ↓
compare v1 node with v2
      ↓
retrieve generation
      ↓
verify stale = true
```

This avoids relying on manually copied IDs and demonstrates the required versioning + staleness path.

## API

### Ingestion

```http
POST /v1/ingest?document_name=ct200
```

Multipart form field:

```text
file=<PDF>
```

Each ingestion creates a new immutable document version. Existing versions are not overwritten.

### Versions

```http
GET /v1/documents/{document_id}/versions
```

### Browse top-level sections

```http
GET /v1/documents/{document_id}/sections
GET /v1/documents/{document_id}/sections?version=1
```

The default is the latest version.

### Get a node

```http
GET /v1/nodes/{node_id}
```

The response contains heading, level, body text, parent, content hash, version ID, position and recursive children.

### Search

```http
GET /v1/search?document_id=1&version=1&q=pressure
```

Searches headings and body text.

### Compare versions

```http
GET /v1/nodes/{node_id}/compare
GET /v1/nodes/{node_id}/compare?against_version=2
```

Returns whether the logical node is unchanged, changed, or removed/unmatched, together with hashes and a small unified diff.

### Create a version-pinned selection

```http
POST /v1/selections
Content-Type: application/json

{
  "name": "Battery QA",
  "document_id": 1,
  "node_ids": [27]
}
```

The selection stores each node ID, its exact version ID, and the source content hash. Re-ingesting the document does not rewrite the old selection.

### Generate QA cases

```http
POST /v1/generations
Content-Type: application/json

{
  "selection_id": 1
}
```

The explicit mock test provider returns three distinct structured cases. The Gemini provider must return 3–5 cases. Pydantic validation checks required fields and ensures every source node ID belongs to the selected source.

Duplicate generation policy: if the same selection already has a generation for the same source fingerprint, the existing generation is returned. Set `force=true` to explicitly create a new generation.

### Retrieve generated tests and staleness

```http
GET /v1/generations?selection_id=1
GET /v1/generations?node_id=27
```

Every retrieved generation contains `stale`. A generation is stale when any selected source node is missing from the current version or its current content hash differs from the stored source hash.

## CT-200 version example

The supplied v1 and v2 PDFs are used directly. The parser currently extracts 27 nodes from v1 and 28 from v2. Version 2 adds `5.3 Data Export` and changes the Battery Life section. The exact extracted text is treated as authoritative by the tests; the implementation does not hardcode those values into the parser.

## Design summary

### Parsing

The parser reads the PDF text layer first because the supplied manuals expose usable text. It uses block order, font size, boldness and section numbering to classify headings, then uses a stack to reconstruct parent/child relationships. Tables are extracted with PyMuPDF table detection. If no headings are recovered from the embedded text, an optional Tesseract OCR fallback is attempted. Embedded images are reported as warnings rather than silently interpreted.

### Version matching

The primary identity is a normalized heading path plus sibling occurrence. This makes duplicate headings distinct and gives an explainable stable identity. During re-ingestion, existing logical keys are matched first. A conservative fuzzy-heading fallback is used only when level and parent path agree and the similarity score has a clear margin. The actual old/new mapping is persisted in `node_matches`.

The limitation is deliberate: major structural moves or ambiguous heading rewrites can still be unmatched. The system reports that condition rather than pretending continuity.

### Staleness

A selection stores the exact source version and content hash. A generation stores a fingerprint over those source hashes, and the JSON document store also keeps the exact source node snapshot used for generation. At retrieval time the source nodes are compared with the latest version. Any missing node or changed hash makes the generation stale.

This is conservative by design. A harmless wording edit and a clinically important threshold change both become stale. The compare endpoint gives a human a lightweight diff for impact review.

## Submission checklist

- `pytest -q` passes.
- Run `python scripts/demo_flow.py` while the API is running.
- Keep the supplied `data/ct200_manual_v1.pdf` and `data/ct200_manual_v2.pdf` unchanged.
- Keep `docs/APPROACH.md` with the repository.
- Use the actual Git repository for the required incremental commit history.
- Do not include caches, virtual environments, SQLite runtime files, generated JSON output, or demo-PDF generators in the submission archive.
