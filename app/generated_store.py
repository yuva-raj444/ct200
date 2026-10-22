import json, os
from pathlib import Path
from .config import settings

def _path(): return Path(settings.generated_store_path)
def put(generation_id, payload):
    p=_path(); data={}
    if p.exists():
        try:data=json.loads(p.read_text())
        except Exception:data={}
    data[str(generation_id)] = payload
    p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(data,indent=2))

def get(generation_id):
    p=_path()
    if not p.exists(): return None
    try:return json.loads(p.read_text()).get(str(generation_id))
    except Exception:return None
