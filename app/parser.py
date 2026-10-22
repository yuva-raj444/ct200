from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import hashlib, re
import fitz
try:
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except Exception:
    OCR_AVAILABLE = False

@dataclass
class ParsedNode:
    heading: str
    level: int
    body_text: str = ""
    page_start: int = 1
    parent_index: int | None = None
    position: int = 0
    logical_key: str = ""
    content_hash: str = ""
    children: list[int] = field(default_factory=list)

@dataclass
class ParsedDocument:
    title: str
    nodes: list[ParsedNode]
    warnings: list[str]

NUMBER_RE = re.compile(r"^(\d+(?:\.\d+)*)[.)]?\s+(.+)$")
LIST_ITEM_RE = re.compile(r"(?:^|\s)(?:\d+)[.)]\s+")

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip()).lower()

def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def numbered_heading(text: str, *, allow_single: bool = True):
    m = NUMBER_RE.match(text.strip())
    if not m:
        return None
    number, label = m.groups()
    # A line containing several numbered items is a list, not a heading.
    if len(LIST_ITEM_RE.findall(text)) > 1:
        return None
    if number.count(".") == 0 and (":" in label or len(label.split()) > 8):
        return None
    if number.count(".") == 0 and not allow_single:
        return None
    if len(label.split()) > 18:
        return None
    return number, label, min(number.count(".") + 1, 6)

def looks_like_heading(text: str, avg_size: float, max_size: float, bold: bool, current_exists: bool = False) -> tuple[bool, int]:
    t = text.strip()
    if not t or len(t) > 180:
        return False, 0
    nh = numbered_heading(t, allow_single=not current_exists)
    if nh:
        return True, nh[2]
    words = t.split()
    titleish = len(words) <= 12 and (bold or avg_size >= max_size * 1.15)
    if titleish and not re.match(r"^[\d\W]+$", t):
        return True, 1
    return False, 0

def append_body(node, text):
    text = text.strip()
    if not text:
        return
    node.body_text += ("\n" if node.body_text else "") + text

def parse_pdf(path: str | Path) -> ParsedDocument:
    path = Path(path)
    doc = fitz.open(path)
    warnings: list[str] = []
    nodes: list[ParsedNode] = []
    current: int | None = None
    title = path.stem.replace("_", " ")
    all_sizes = [sp.get("size", 0) for p in doc for b in p.get_text("dict").get("blocks", []) for line in b.get("lines", []) for sp in line.get("spans", [])]
    max_size = max(all_sizes or [12])
    stack: list[int] = []
    position = 0

    for pno, page in enumerate(doc, 1):
        # Keep only substantial tables; PyMuPDF can also report individual cells as tiny tables.
        real_tables = []
        try:
            for t in page.find_tables().tables:
                cells = t.extract()
                if len(cells) >= 2 and max((len(r) for r in cells), default=0) >= 2:
                    area = max(0, (t.bbox[2]-t.bbox[0]) * (t.bbox[3]-t.bbox[1]))
                    real_tables.append((area, t.bbox, cells))
            real_tables.sort(reverse=True, key=lambda x: x[0])
        except Exception:
            pass
        table_bboxes = [tuple(x[1]) for x in real_tables[:3]]
        table_written = [False] * len(table_bboxes)

        for block in page.get_text("blocks", sort=True):
            raw = block[4].strip()
            if not raw:
                continue
            bbox = tuple(block[:4])
            table_index = next((i for i,tb in enumerate(table_bboxes) if bbox[0] >= tb[0]-2 and bbox[1] >= tb[1]-2 and bbox[2] <= tb[2]+2 and bbox[3] <= tb[3]+2), None)
            if table_index is not None:
                if current is None:
                    warnings.append(f"page {pno}: table appeared before first heading")
                elif not table_written[table_index]:
                    cells = real_tables[table_index][2]
                    rows = []
                    for row in cells:
                        vals = [re.sub(r"\s+", " ", str(v or "")).strip() for v in row]
                        rows.append("| " + " | ".join(vals) + " |")
                    if rows:
                        append_body(nodes[current], "[TABLE]\n" + "\n".join(rows))
                    table_written[table_index] = True
                continue

            lines = [x.strip() for x in raw.splitlines() if x.strip()]
            info = page.get_text("dict", clip=fitz.Rect(block[:4]))
            sizes = [sp.get("size", 0) for bb in info.get("blocks", []) for line in bb.get("lines", []) for sp in line.get("spans", [])]
            bold = any(bool(sp.get("flags", 0) & 16) for bb in info.get("blocks", []) for line in bb.get("lines", []) for sp in line.get("spans", []))
            avg = sum(sizes) / len(sizes) if sizes else 0

            for line in lines:
                is_heading, level = looks_like_heading(line, avg, max_size, bold, current is not None)
                if is_heading:
                    heading = line
                    while stack and nodes[stack[-1]].level >= level:
                        stack.pop()
                    parent = stack[-1] if stack else None
                    sibling_count = 1
                    siblings = nodes[parent].children if parent is not None else [i for i,n in enumerate(nodes) if n.parent_index is None]
                    sibling_count += sum(1 for ci in siblings if norm(nodes[ci].heading) == norm(heading))
                    path_parts = [nodes[i].logical_key for i in stack]
                    logical_key = "/".join(path_parts + [f"{norm(heading)}#{sibling_count}"])
                    node = ParsedNode(heading, level, "", pno, parent, position, logical_key)
                    nodes.append(node)
                    idx = len(nodes) - 1
                    position += 1
                    if parent is not None:
                        nodes[parent].children.append(idx)
                    stack.append(idx)
                    current = idx
                else:
                    if current is None:
                        title = line[:200]
                        warnings.append(f"page {pno}: body text appeared before the first heading")
                        continue
                    # Preserve list items as body content rather than manufacturing sections.
                    append_body(nodes[current], line)

    # The manual title can wrap across multiple PDF text blocks. Keep it as the
    # document title rather than manufacturing multiple root sections.
    first_section = next((i for i,n in enumerate(nodes) if NUMBER_RE.match(n.heading)), None)
    if first_section is not None and first_section > 0:
        title_parts = [n.heading for n in nodes[:first_section]]
        title = " ".join(title_parts)
        nodes = nodes[first_section:]
        for i, n in enumerate(nodes):
            n.position = i
            n.parent_index = (n.parent_index - first_section) if n.parent_index is not None and n.parent_index >= first_section else None
            n.children = [c-first_section for c in n.children if c >= first_section]

    if not nodes:
        warnings.append("No headings detected from embedded PDF text; attempting OCR fallback.")
        if OCR_AVAILABLE:
            ocr_doc = fitz.open(path)
            for pno, page in enumerate(ocr_doc, 1):
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                txt = pytesseract.image_to_string(img)
                for line in [x.strip() for x in txt.splitlines() if x.strip()]:
                    nh = numbered_heading(line)
                    if nh:
                        number, label, level = nh
                        node = ParsedNode(line, level, "", pno, None, position, f"{norm(line)}#ocr{position}")
                        nodes.append(node); position += 1
                    elif nodes:
                        append_body(nodes[-1], line)
            ocr_doc.close()
            if nodes:
                warnings.append("OCR fallback was used because the PDF text layer did not expose headings.")
        else:
            warnings.append("OCR fallback unavailable; install Tesseract and pytesseract for scanned PDFs.")

    for n in nodes:
        n.content_hash = sha(norm(n.heading) + "\n" + norm(n.body_text))
    for pno, page in enumerate(doc, 1):
        if page.get_images(full=True):
            warnings.append(f"page {pno}: embedded image(s) detected; visual OCR/caption extraction may need review")
    doc.close()
    return ParsedDocument(title, nodes, warnings)
