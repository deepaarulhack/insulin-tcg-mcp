from pydantic import BaseModel
from typing import List, Optional

class RequirementRequest(BaseModel):
    prompt: str

class RequirementResponse(BaseModel):
    req_id: str
    requirement_text: str

class TestCaseRequest(BaseModel):
    req_id: str

class TestCaseResponse(BaseModel):
    test_case_id: str
    title: str
    description: str
    steps: List[str]
    expected_results: List[str]

class SamplesRequest(BaseModel):
    req_id: str
    test_case_ids: List[str]

class SamplesResponse(BaseModel):
    test_case_id: str
    sample_path: str

class JUnitRequest(BaseModel):
    req_id: str
    test_case_ids: List[str]

class JUnitResponse(BaseModel):
    test_case_id: str
    junit_path: str
    sample_path: Optional[str]

class TestResultsRequest(BaseModel):
    req_id: str

class TestResult(BaseModel):
    req_id: str
    test_case_id: str
    status: str
    message: str
    sample_path: Optional[str]
    recorded_at: str

class TestResultsResponse(BaseModel):
    inserted: int
    results: List[TestResult]

class JiraRequest(BaseModel):
    req_id: str
    test_case_ids: List[str]
    run_id: Optional[str]

class JiraResponse(BaseModel):
    issue_key: str
    url: str
