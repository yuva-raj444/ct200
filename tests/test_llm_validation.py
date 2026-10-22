import pytest
from app.llm import validate_payload

def test_rejects_wrong_case_count():
    with pytest.raises(ValueError): validate_payload({'test_cases':[]},{1})

def test_rejects_unknown_source_node():
    payload={'test_cases':[{'title':'a','preconditions':[],'steps':['x'],'expected_result':'y','source_node_ids':[9]}]*3}
    with pytest.raises(ValueError): validate_payload(payload,{1})
