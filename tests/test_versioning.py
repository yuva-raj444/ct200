from types import SimpleNamespace
from app.versioning import match_nodes

def n(id,key,heading,level=1): return SimpleNamespace(id=id,logical_key=key,heading=heading,level=level)

def test_exact_logical_match():
    old=[n(1,'1 safety#1','Safety')]; new=[n(2,'1 safety#1','Safety')]
    assert match_nodes(old,new)=={2:1}

def test_fuzzy_fallback_is_conservative():
    old=[n(1,'1 safety#1','Safety precautions')]; new=[n(2,'1 safety#1','Safety precaution')]
    assert match_nodes(old,new)=={2:1}
