from __future__ import annotations

import difflib
import hashlib
import json
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db, init_db
from .generated_store import get as store_get
from .generated_store import put as store_put
from .llm import generate
from .models import Document, DocumentVersion, Generation, Node, NodeMatch, Selection, SelectionItem
from .parser import parse_pdf
from .schemas import GenerateRequest, GenerationOut, IngestOut, NodeOut, SelectionCreate, SelectionOut
from .versioning import match_nodes

app = FastAPI(title="CT-200 Traceable QA API", version="1.0.0")


@app.on_event("startup")
def startup() -> None:
    init_db()


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def latest_version(db: Session, document_id: int) -> DocumentVersion | None:
    return db.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.desc())
    )


def get_version(db: Session, document_id: int, version: int | None) -> DocumentVersion:
    target = version
    if target is None:
        latest = latest_version(db, document_id)
        target = latest.version_number if latest else None
    if target is None:
        raise HTTPException(404, "document/version not found")
    found = db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.version_number == target,
        )
    )
    if not found:
        raise HTTPException(404, "version not found")
    return found


def tree_node(node: Node) -> NodeOut:
    return NodeOut(
        id=node.id,
        heading=node.heading,
        level=node.level,
        body_text=node.body_text,
        content_hash=node.content_hash,
        parent_id=node.parent_id,
        version_id=node.version_id,
        position=node.position,
        children=[tree_node(child) for child in sorted(node.children, key=lambda x: x.position)],
    )


def current_counterpart(db: Session, node: Node) -> Node | None:
    version = db.get(DocumentVersion, node.version_id)
    latest = latest_version(db, version.document_id)
    if latest is None:
        return None
    return db.scalar(
        select(Node).where(
            Node.version_id == latest.id,
            Node.logical_key == node.logical_key,
        )
    )


def stale_generation(db: Session, generation: Generation) -> bool:
    selection = db.get(Selection, generation.selection_id)
    if selection is None:
        return True

    for item in selection.items:
        source_node = db.get(Node, item.node_id)
        if source_node is None:
            return True
        current = current_counterpart(db, source_node)
        if current is None or current.content_hash != item.node_content_hash:
            return True
    return False


def generation_out(db: Session, generation: Generation) -> GenerationOut:
    stored = store_get(generation.id)
    cases = stored.get("test_cases") if stored else json.loads(generation.output_json)
    return GenerationOut(
        id=generation.id,
        selection_id=generation.selection_id,
        status=generation.status,
        source_fingerprint=generation.source_fingerprint,
        provider=generation.provider,
        model=generation.model,
        test_cases=cases,
        stale=stale_generation(db, generation),
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/v1/ingest", response_model=IngestOut)
async def ingest(
    file: UploadFile = File(...),
    document_name: str = Query("ct200", min_length=1),
    db: Session = Depends(get_db),
):
    data = await file.read()
    if not data:
        raise HTTPException(400, "uploaded file is empty")
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "only PDF files are supported")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(data)
        temp_path = Path(tmp.name)

    try:
        parsed = parse_pdf(temp_path)
    finally:
        temp_path.unlink(missing_ok=True)

    if not parsed.nodes:
        raise HTTPException(422, "no document headings could be extracted")

    document = db.scalar(select(Document).where(Document.name == document_name))
    if document is None:
        document = Document(name=document_name)
        db.add(document)
        db.flush()

    previous = latest_version(db, document.id)
    version_number = previous.version_number + 1 if previous else 1

    version = DocumentVersion(
        document_id=document.id,
        version_number=version_number,
        source_filename=file.filename,
        source_sha256=file_hash(data),
    )
    db.add(version)
    db.flush()

    db_nodes: list[Node] = []
    for parsed_node in parsed.nodes:
        parent = db_nodes[parsed_node.parent_index] if parsed_node.parent_index is not None else None
        node = Node(
            version_id=version.id,
            logical_key=parsed_node.logical_key,
            heading=parsed_node.heading,
            level=parsed_node.level,
            body_text=parsed_node.body_text,
            content_hash=parsed_node.content_hash,
            parent=parent,
            position=parsed_node.position,
        )
        db.add(node)
        db_nodes.append(node)
    db.flush()

    if previous is not None:
        old_nodes = list(previous.nodes)
        mapping = match_nodes(old_nodes, db_nodes)
        old_by_id = {node.id: node for node in old_nodes}
        for new_id, old_id in mapping.items():
            new_node = next(node for node in db_nodes if node.id == new_id)
            old_node = old_by_id[old_id]
            # Keep the old logical identity when a conservative fuzzy match was used.
            new_node.logical_key = old_node.logical_key
            db.add(
                NodeMatch(
                    from_node_id=old_node.id,
                    to_node_id=new_node.id,
                    match_type="logical_key"
                    if old_node.logical_key == parsed.nodes[new_node.position].logical_key
                    else "fuzzy_heading",
                    changed=old_node.content_hash != new_node.content_hash,
                )
            )

    db.commit()
    return IngestOut(
        document_id=document.id,
        version_id=version.id,
        version_number=version_number,
        node_count=len(db_nodes),
        warnings=parsed.warnings,
    )


@app.get("/v1/documents/{document_id}/versions")
def versions(document_id: int, db: Session = Depends(get_db)):
    if db.get(Document, document_id) is None:
        raise HTTPException(404, "document not found")
    return [
        {
            "id": version.id,
            "version": version.version_number,
            "source": version.source_filename,
            "sha256": version.source_sha256,
        }
        for version in db.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_number)
        ).all()
    ]


@app.get("/v1/documents/{document_id}/sections")
def sections(document_id: int, version: int | None = None, db: Session = Depends(get_db)):
    selected_version = get_version(db, document_id, version)
    roots = db.scalars(
        select(Node)
        .where(Node.version_id == selected_version.id, Node.parent_id.is_(None))
        .order_by(Node.position)
    ).all()
    return [tree_node(node) for node in roots]


@app.get("/v1/nodes/{node_id}", response_model=NodeOut)
def get_node(node_id: int, db: Session = Depends(get_db)):
    node = db.get(Node, node_id)
    if node is None:
        raise HTTPException(404, "node not found")
    return tree_node(node)


@app.get("/v1/search")
def search(document_id: int, q: str, version: int | None = None, db: Session = Depends(get_db)):
    if not q.strip():
        raise HTTPException(400, "q must not be empty")
    selected_version = get_version(db, document_id, version)
    pattern = f"%{q}%"
    rows = db.scalars(
        select(Node)
        .where(
            Node.version_id == selected_version.id,
            or_(Node.heading.ilike(pattern), Node.body_text.ilike(pattern)),
        )
        .order_by(Node.position)
    ).all()
    return [
        {
            "id": node.id,
            "heading": node.heading,
            "level": node.level,
            "snippet": node.body_text[:300],
            "hash": node.content_hash,
        }
        for node in rows
    ]


@app.get("/v1/nodes/{node_id}/compare")
def compare(node_id: int, against_version: int | None = None, db: Session = Depends(get_db)):
    node = db.get(Node, node_id)
    if node is None:
        raise HTTPException(404, "node not found")
    source_version = db.get(DocumentVersion, node.version_id)
    target_version = get_version(db, source_version.document_id, against_version)

    target = db.scalar(
        select(Node).where(
            Node.version_id == target_version.id,
            Node.logical_key == node.logical_key,
        )
    )
    if target is None:
        return {
            "changed": True,
            "status": "removed_or_unmatched",
            "from_version": source_version.version_number,
            "to_version": target_version.version_number,
            "from_hash": node.content_hash,
            "to_hash": None,
            "diff": [],
        }

    if target.content_hash == node.content_hash:
        return {
            "changed": False,
            "status": "unchanged",
            "from_version": source_version.version_number,
            "to_version": target_version.version_number,
            "from_hash": node.content_hash,
            "to_hash": target.content_hash,
            "diff": [],
        }

    diff = list(
        difflib.unified_diff(
            node.body_text.splitlines(),
            target.body_text.splitlines(),
            fromfile=f"v{source_version.version_number}",
            tofile=f"v{target_version.version_number}",
            lineterm="",
        )
    )
    return {
        "changed": True,
        "status": "changed",
        "from_version": source_version.version_number,
        "to_version": target_version.version_number,
        "from_hash": node.content_hash,
        "to_hash": target.content_hash,
        "diff": diff[:80],
    }


@app.post("/v1/selections", response_model=SelectionOut)
def create_selection(req: SelectionCreate, db: Session = Depends(get_db)):
    requested_ids = list(dict.fromkeys(req.node_ids))
    nodes = [db.get(Node, node_id) for node_id in requested_ids]
    if any(node is None for node in nodes):
        raise HTTPException(404, "one or more nodes not found")
    if any(db.get(DocumentVersion, node.version_id).document_id != req.document_id for node in nodes):
        raise HTTPException(400, "all nodes must belong to the requested document")

    selection = Selection(name=req.name, document_id=req.document_id)
    db.add(selection)
    db.flush()
    for node in nodes:
        db.add(
            SelectionItem(
                selection_id=selection.id,
                node_id=node.id,
                version_id=node.version_id,
                node_content_hash=node.content_hash,
            )
        )
    db.commit()
    return SelectionOut(
        id=selection.id,
        name=selection.name,
        document_id=selection.document_id,
        version_ids=[node.version_id for node in nodes],
    )


@app.get("/v1/selections/{selection_id}")
def get_selection(selection_id: int, db: Session = Depends(get_db)):
    selection = db.get(Selection, selection_id)
    if selection is None:
        raise HTTPException(404, "selection not found")
    return {
        "id": selection.id,
        "name": selection.name,
        "document_id": selection.document_id,
        "items": [
            {
                "node_id": item.node_id,
                "version_id": item.version_id,
                "content_hash": item.node_content_hash,
            }
            for item in selection.items
        ],
    }


@app.post("/v1/generations", response_model=GenerationOut)
async def generate_tests(req: GenerateRequest, db: Session = Depends(get_db)):
    selection = db.get(Selection, req.selection_id)
    if selection is None:
        raise HTTPException(404, "selection not found")

    items = sorted(selection.items, key=lambda item: item.id)
    source = []
    for item in items:
        node = db.get(Node, item.node_id)
        if node is None:
            raise HTTPException(409, "selection references a missing node")
        source.append(
            {
                "id": node.id,
                "heading": node.heading,
                "body_text": node.body_text,
                "content_hash": node.content_hash,
                "version_id": node.version_id,
            }
        )

    fingerprint = hashlib.sha256(
        "|".join(item["content_hash"] for item in source).encode("utf-8")
    ).hexdigest()

    if not req.force:
        existing = db.scalar(
            select(Generation)
            .where(
                Generation.selection_id == selection.id,
                Generation.source_fingerprint == fingerprint,
            )
            .order_by(Generation.id.desc())
        )
        if existing:
            return generation_out(db, existing)

    try:
        cases = await generate(source)
    except Exception as exc:
        raise HTTPException(502, f"LLM generation failed: {exc}") from exc

    payload = [case.model_dump() for case in cases]
    generation = Generation(
        selection_id=selection.id,
        source_fingerprint=fingerprint,
        provider=settings.llm_provider,
        model=settings.llm_model,
        status="complete",
        output_json=json.dumps(payload),
    )
    db.add(generation)
    db.commit()
    db.refresh(generation)

    store_put(
        generation.id,
        {
            "selection_id": selection.id,
            "source_fingerprint": fingerprint,
            "source_nodes": source,
            "test_cases": payload,
        },
    )
    return generation_out(db, generation)


@app.get("/v1/generations")
def generations(
    selection_id: int | None = None,
    node_id: int | None = None,
    db: Session = Depends(get_db),
):
    rows = db.scalars(select(Generation).order_by(Generation.id.desc())).all()
    result = []
    for generation in rows:
        if selection_id is not None and generation.selection_id != selection_id:
            continue
        if node_id is not None:
            selection = db.get(Selection, generation.selection_id)
            if selection is None or not any(item.node_id == node_id for item in selection.items):
                continue
        result.append(generation_out(db, generation))
    return result
