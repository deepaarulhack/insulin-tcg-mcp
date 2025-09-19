import uuid
import requests
from google.cloud import secretmanager
from datetime import datetime
from fastapi import FastAPI
from pydantic import BaseModel
from google.cloud import bigquery
from typing import List, Dict
from google.cloud import storage

app = FastAPI(title="Insulin TCG MCP")

# init BigQuery client
bq = bigquery.Client()

storage_client = storage.Client()
bucket_name = f"{bq.project}-tcg"
bucket = storage_client.bucket(bucket_name)


# init secret manager
secret_client = secretmanager.SecretManagerServiceClient()

# input model for requirement.generate
class RequirementRequest(BaseModel):
    prompt: str
    source_repo: str | None = None

# output model
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

class ISORequest(BaseModel):
    test_case_ids: List[str]

class ISOResult(BaseModel):
    test_case_id: str
    compliant: bool
    missing_elements: List[str]
    related_iso_refs: List[str]
    suggestions: str
    
class PytestRequest(BaseModel):
    req_id: str
    test_case_ids: List[str]

class PytestResult(BaseModel):
    test_case_id: str
    pytest_path: str

class SamplesRequest(BaseModel):
    req_id: str
    test_case_ids: List[str]

class SamplesResult(BaseModel):
    test_case_id: str
    sample_path: str


def access_secret(secret_name: str) -> str:
    name = f"projects/{bq.project}/secrets/{secret_name}/versions/latest"
    return secret_client.access_secret_version(request={"name": name}).payload.data.decode("UTF-8")
# read secrets
JIRA_USER = access_secret("jira-user")
JIRA_TOKEN = access_secret("jira-token")
JIRA_URL = access_secret("jira-url")


class JiraRequest(BaseModel):
    req_id: str
    test_case_ids: List[str]
    run_id: str | None = None

class JiraResponse(BaseModel):
    issue_key: str
    url: str


@app.post("/tools/requirement.generate", response_model=RequirementResponse)
def requirement_generate(req: RequirementRequest):
    """
    Stub for requirement.generate MCP tool.
    For now it just echoes requirement_text = prompt.
    Later we'll plug Vertex AI here.
    """
    req_id = f"REQ-{uuid.uuid4().hex[:8].upper()}"
    requirement_text = req.prompt.strip()

    # save into BigQuery table
    table_ref = "insulin-tcg-mcp.qa_metrics.requirements"
    rows = [{
        "req_id": req_id,
        "prompt": req.prompt,
        "requirement_text": requirement_text,
        "source_repo": req.source_repo or "",
        "created_at": datetime.utcnow().isoformat()
    }]
    errors = bq.insert_rows_json(table_ref, rows)
    if errors:
        raise Exception(f"BigQuery insert error: {errors}")

    return RequirementResponse(req_id=req_id, requirement_text=requirement_text)


@app.post("/tools/testcase.generate")
def testcase_generate(req: TestCaseRequest) -> List[TestCaseResponse]:
    """
    Stub for testcase.generate MCP tool.
    For now, generates 2 fake test cases for the given requirement.
    Later we’ll replace with Vertex AI output.
    """
    # lookup requirement text from BigQuery
    query = f"""
      SELECT requirement_text
      FROM `insulin-tcg-mcp.qa_metrics.requirements`
      WHERE req_id = '{req.req_id}'
    """
    rows = list(bq.query(query).result())
    if not rows:
        return []

    requirement_text = rows[0]["requirement_text"]

    # create fake test cases
    tc1 = {
        "test_case_id": f"TC-{uuid.uuid4().hex[:6].upper()}",
        "title": "Verify basal delivery within tolerance",
        "description": f"Check if: {requirement_text}",
        "steps": ["Set basal rate to 0.5 U/hr", "Measure over 1 hour"],
        "expected_results": ["Delivery is within ±5% of setpoint"]
    }
    tc2 = {
        "test_case_id": f"TC-{uuid.uuid4().hex[:6].upper()}",
        "title": "Verify alarm on deviation",
        "description": f"Check alarm triggers if outside ±5% for: {requirement_text}",
        "steps": ["Simulate delivery error >5%", "Monitor alarm system"],
        "expected_results": ["Alarm is raised within 60s"]
    }

    # save to BigQuery
    # add req_id before saving

    for tc in (tc1, tc2):
        tc["req_id"] = req.req_id

    table_ref = "insulin-tcg-mcp.qa_metrics.test_cases"
    bq.insert_rows_json(table_ref, [tc1, tc2])

    return [tc1, tc2]

@app.post("/tools/iso.validate")
def iso_validate(req: ISORequest) -> List[ISOResult]:
    """
    Stub for iso.validate MCP tool.
    For now, marks all test cases as compliant except one missing element demo.
    Later we’ll plug Vertex AI for real ISO validation.
    """
    results = []
    for tc_id in req.test_case_ids:
        compliant = True
        missing = []
        refs = ["ISO 62304 §5.5.1", "ISO 14971 §7.4"]
        suggestion = "Looks good."

        # simulate one missing element if ID ends with 'E'
        if tc_id.endswith("E"):
            compliant = False
            missing = ["Acceptance criteria not detailed"]
            suggestion = "Add precise acceptance criteria."

        res = {
            "test_case_id": tc_id,
            "compliant": compliant,
            "missing_elements": missing,
            "related_iso_refs": refs,
            "suggestions": suggestion,
        }
        results.append(res)

    # save to BigQuery
    table_ref = "insulin-tcg-mcp.qa_metrics.iso_validation"
    rows = []
    for r in results:
        rows.append({
            "validation_id": f"VAL-{uuid.uuid4().hex[:8].upper()}",
            **r,
            "validated_at": datetime.utcnow().isoformat()
        })
    bq.insert_rows_json(table_ref, rows)

    return results

@app.post("/tools/pytest.generate")
def pytest_generate(req: PytestRequest) -> List[PytestResult]:
    """
    Generate simple pytest scripts for given test cases and save to GCS.
    """
    results = []

    # fetch test cases from BigQuery
    ids = ",".join([f"'{tc}'" for tc in req.test_case_ids])
    query = f"""
      SELECT test_case_id, title, steps, expected_results
      FROM `insulin-tcg-mcp.qa_metrics.test_cases`
      WHERE req_id = '{req.req_id}' AND test_case_id IN ({ids})
    """
    rows = list(bq.query(query).result())

    for row in rows:
        tc_id = row["test_case_id"]
        title = row["title"]
        steps = row["steps"]
        expects = row["expected_results"]

        # create pytest function text
        fn_name = "test_" + tc_id.lower().replace("-", "_")
        steps_comment = "\n    ".join([f"# STEP: {s}" for s in steps])
        expects_comment = "\n    ".join([f"# EXPECT: {e}" for e in expects])

        py_code = f"""import pytest

def {fn_name}():
    \"\"\"{title}\"\"\"
    {steps_comment}
    {expects_comment}
    # TODO: replace with real assertions
    assert True
"""

        # upload to GCS
        blob_path = f"artifacts/pytest/{req.req_id}/{tc_id}.py"
        blob = bucket.blob(blob_path)
        blob.upload_from_string(py_code, content_type="text/x-python")

        results.append({
            "test_case_id": tc_id,
            "pytest_path": f"gs://{bucket_name}/{blob_path}"
        })

    return results

@app.post("/tools/samples.generate")
def samples_generate(req: SamplesRequest) -> List[SamplesResult]:
    """
    Generate simple sample input/output JSONs for test cases and save to GCS.
    """
    results = []
    for tc_id in req.test_case_ids:
        # fake sample data
        sample_data = {
            "test_case_id": tc_id,
            "inputs": {"glucose_level": 250, "basal_rate": 0.5},
            "expected_output": {"alarm_triggered": True, "delivery_ok": False}
        }

        # upload to GCS
        blob_path = f"artifacts/samples/{req.req_id}/{tc_id}.json"
        blob = bucket.blob(blob_path)
        blob.upload_from_string(
            str(sample_data),
            content_type="application/json"
        )

        results.append({
            "test_case_id": tc_id,
            "sample_path": f"gs://{bucket_name}/{blob_path}"
        })

    return results



@app.post("/tools/jira.update")
def jira_update(req: JiraRequest) -> JiraResponse:
    """
    Create a Jira issue for the requirement + test cases + artifacts.
    """
    # fetch requirement
    query = f"""
      SELECT requirement_text
      FROM `insulin-tcg-mcp.qa_metrics.requirements`
      WHERE req_id = '{req.req_id}'
    """
    rows = list(bq.query(query).result())
    if not rows:
        raise Exception("Requirement not found")
    requirement_text = rows[0]["requirement_text"]

    # fetch test cases
    ids = ",".join([f"'{tc}'" for tc in req.test_case_ids])
    query = f"""
      SELECT test_case_id, title, description
      FROM `insulin-tcg-mcp.qa_metrics.test_cases`
      WHERE test_case_id IN ({ids})
    """
    tcs = list(bq.query(query).result())

    # build Jira issue payload
    description_lines = [f"*Requirement*: {requirement_text}", ""]
    for tc in tcs:
        description_lines.append(f"*{tc['test_case_id']}*: {tc['title']} — {tc['description']}")
    description = "\n".join(description_lines)

    issue_payload = {
        "fields": {
            "project": {"key": "KAN"},
            "summary": f"Requirement {req.req_id} validation",
            "description": description,
            "issuetype": {"name": "Task"}
        }
    }

    response = requests.post(
        f"{JIRA_URL}/rest/api/2/issue",
        json=issue_payload,
        auth=(JIRA_USER, JIRA_TOKEN),
        headers={"Content-Type": "application/json"}
    )
    if response.status_code not in (200, 201):
        raise Exception(f"Jira error {response.status_code}: {response.text}")

    issue_key = response.json()["key"]
    return JiraResponse(issue_key=issue_key, url=f"{JIRA_URL}/browse/{issue_key}")


@app.get("/")
def root():
    return {"message": "MCP server is running"}

