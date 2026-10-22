from pydantic import BaseModel, ConfigDict, Field


class NodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    heading: str
    level: int
    body_text: str
    content_hash: str
    parent_id: int | None
    version_id: int
    position: int
    children: list["NodeOut"] = Field(default_factory=list)


class SelectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    document_id: int
    node_ids: list[int] = Field(min_length=1)


class SelectionOut(BaseModel):
    id: int
    name: str
    document_id: int
    version_ids: list[int]


class GenerateRequest(BaseModel):
    selection_id: int
    force: bool = False


class TestCase(BaseModel):
    title: str = Field(min_length=1)
    preconditions: list[str] = Field(default_factory=list)
    steps: list[str] = Field(min_length=1)
    expected_result: str = Field(min_length=1)
    source_node_ids: list[int] = Field(min_length=1)


class GenerationOut(BaseModel):
    id: int
    selection_id: int
    status: str
    source_fingerprint: str
    provider: str
    model: str
    test_cases: list[TestCase]
    stale: bool


class IngestOut(BaseModel):
    document_id: int
    version_id: int
    version_number: int
    node_count: int
    warnings: list[str]
