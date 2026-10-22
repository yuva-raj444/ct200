from pathlib import Path
import fitz
from app.parser import parse_pdf

def make_pdf(path, pages):
    d=fitz.open()
    for text in pages:
        p=d.new_page(); p.insert_text((50,50),text,fontsize=12)
    d.save(path); d.close()

def test_duplicate_heading_gets_distinct_nodes(tmp_path):
    p=tmp_path/'dup.pdf'
    make_pdf(p,["1 Safety\nShared warning\n1.1 Warning\nFirst", "1.2 Warning\nSecond"])
    doc=parse_pdf(p)
    warnings=[n for n in doc.nodes if n.heading.lower().endswith('warning')]
    assert len(warnings)==2
    assert warnings[0].logical_key != warnings[1].logical_key

def test_nested_parent_child(tmp_path):
    p=tmp_path/'tree.pdf'; make_pdf(p,["1 Safety\ntext\n1.1 Warning\nwarn\n1.1.1 Electrical\nelectrical"])
    doc=parse_pdf(p)
    assert [n.level for n in doc.nodes[:3]] == [1,2,3]
    assert doc.nodes[1].parent_index==0 and doc.nodes[2].parent_index==1

def test_body_does_not_become_fake_section(tmp_path):
    p=tmp_path/'body.pdf'; make_pdf(p,["1 Operation\nThis is a long sentence describing normal operation and should remain body text."])
    doc=parse_pdf(p)
    assert len(doc.nodes)==1
    assert 'long sentence' in doc.nodes[0].body_text
