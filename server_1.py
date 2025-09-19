import uuid
import json
import requests
import re
from google.cloud import secretmanager
from datetime import datetime
from fastapi import FastAPI, Body
from pydantic import BaseModel
from google.cloud import bigquery, storage
from typing import List
from langchain_google_vertexai import ChatVertexAI
from langgraph.prebuilt import create_react_agent
from langgraph.prebuilt.chat_agent_executor import AgentState
from vertexai.generative_models import GenerativeModel
from langchain.tools import StructuredTool
import os
import uvicorn

app = FastAPI(title="Insulin TCG MCP")

# ============================
# GCP Clients
# ============================
bq = bigquery.Client()
storage_client = storage.Client()
bucket_name = f"{bq.project}-tcg"
bucket = storage_client.bucket(bucket_name)
secret_client = secretmanager.SecretManagerServiceClient()

def access_secret(secret_name: str) -> str:
    name = f"projects/{bq.project}/secrets/{secret_name}/versions/latest"
    return secret_client.access_secret_version(
        request={"name": name}
    ).payload.data.decode("UTF-8")

# Jira secrets
JIRA_USER = access_secret("jira-user")
JIRA_TOKEN = access_secret("jira-token")
JIRA_URL = access_secret("jira-url")

# ============================
# Pydantic Models
# ============================
class RequirementRequest(BaseModel):
    prompt: str
    source_repo: str | None = None

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

class JiraRequest(BaseModel):
    req_id: str
    test_case_ids: List[str]
    run_id: str | None = None

class JiraResponse(BaseModel):
    issue_key: str
    url: str

# ============================
# LangGraph Agent + Tools
# ============================
class RequirementInput(BaseModel):
    prompt: str

class TestcaseInput(BaseModel):
    req_id: str

class ISOInput(BaseModel):
    test_case_ids: List[str]

class PytestInput(BaseModel):
    req_id: str
    test_case_ids: List[str]

class SamplesInput(BaseModel):
    req_id: str
    test_case_ids: List[str]

class JiraInput(BaseModel):
    req_id: str
    test_case_ids: List[str]
    run_id: str | None = None

tools = [
    StructuredTool.from_function(
        func=lambda prompt: json.dumps(
            requirement_generate(RequirementRequest(prompt=prompt)).dict()
        ),
        name="requirement_generator",
        description="Generate a structured requirement from a natural language prompt.",
        args_schema=RequirementInput
    ),
    StructuredTool.from_function(
        func=lambda req_id: json.dumps(
            [tc for tc in testcase_generate(TestCaseRequest(req_id=req_id))]
        ),
        name="testcase_generator",
        description="Generate test cases for a given requirement ID.",
        args_schema=TestcaseInput
    ),
    StructuredTool.from_function(
        func=lambda test_case_ids: json.dumps(
            [res for res in iso_validate(ISORequest(test_case_ids=test_case_ids))]
        ),
        name="iso_validator",
        description="Validate test cases against ISO standards.",
        args_schema=ISOInput
    ),
    StructuredTool.from_function(
        func=lambda req_id, test_case_ids: json.dumps(
            [res for res in pytest_generate(PytestRequest(req_id=req_id, test_case_ids=test_case_ids))]
        ),
        name="pytest_generator",
        description="Generate pytest scripts for validated test cases.",
        args_schema=PytestInput
    ),
    StructuredTool.from_function(
        func=lambda req_id, test_case_ids: json.dumps(
            [res for res in samples_generate(SamplesRequest(req_id=req_id, test_case_ids=test_case_ids))]
        ),
        name="sample_generator",
        description="Generate sample datasets for test cases.",
        args_schema=SamplesInput
    ),
    StructuredTool.from_function(
        func=lambda req_id, test_case_ids, run_id=None: json.dumps(
            jira_update(JiraRequest(req_id=req_id, test_case_ids=test_case_ids, run_id=run_id)).dict()
        ),
        name="jira_updater",
        description="Update Jira with test case results.",
        args_schema=JiraInput
    ),
]

llm = ChatVertexAI(model="gemini-2.5-pro", temperature=0)
agent = create_react_agent(llm, tools, state_schema=AgentState)
model = GenerativeModel("gemini-2.5-pro")

# ============================
# Helper: Repo Context from GCS
# ============================
def get_repo_context():
    context = ""
    prefix = "repo/Personal_Insulin_Pump-Integrated_System/"
    blobs = storage_client.list_blobs(bucket_name, prefix=prefix)
    java_files = [b for b in blobs if b.name.endswith(".java")]

    for blob in java_files[:5]:
        try:
            content = blob.download_as_text()
            context += f"\n--- {blob.name} ---\n" + "\n".join(content.splitlines()[:80])
        except Exception:
            continue

    return context

# ============================
# JSON Safe Parser
# ============================
def safe_parse_json(answer_text, requirement_text, req_id):
    try:
        # Extract JSON array if mixed with text
        match = re.search(r"\[.*\]", answer_text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return json.loads(answer_text)
    except Exception:
        # Fallback if parsing fails
        return [{
            "test_case_id": f"TC-{uuid.uuid4().hex[:6].upper()}",
            "title": "Fallback test case",
            "description": requirement_text,
            "steps": ["Step 1", "Step 2"],
            "expected_results": ["Expected outcome"],
            "req_id": req_id
        }]

# ============================
# Debug Endpoint
# ============================
@app.get("/tools/debug.context")
def debug_context():
    prefix = "repo/Personal_Insulin_Pump-Integrated_System/"
    blobs = storage_client.list_blobs(bucket_name, prefix=prefix)
    files = [b.name for b in blobs if b.name.endswith(".java")]
    return {"java_files_found": files[:10], "total_files": len(files)}

# ============================
# MCP Tools
# ============================
@app.post("/tools/requirement.generate", response_model=RequirementResponse)
def requirement_generate(req: RequirementRequest):
    req_id = f"REQ-{uuid.uuid4().hex[:8].upper()}"
    requirement_text = req.prompt.strip()
    project_id = bq.project
    table_ref = f"{project_id}.qa_metrics.requirements"
    rows = [{
        "req_id": req_id,
        "prompt": req.prompt,
        "requirement_text": requirement_text,
        "source_repo": req.source_repo or "",
        "created_at": datetime.utcnow().isoformat(),
    }]
    errors = bq.insert_rows_json(table_ref, rows)
    if errors:
        raise Exception(f"BigQuery insert error: {errors}")
    return RequirementResponse(req_id=req_id, requirement_text=requirement_text)

@app.post("/tools/testcase.generate")
def testcase_generate(req: TestCaseRequest) -> List[TestCaseResponse]:
    query = f"""
      SELECT requirement_text
      FROM `insulin-tcg-mcp.qa_metrics.requirements`
      WHERE req_id = '{req.req_id}'
    """
    rows = list(bq.query(query).result())
    if not rows:
        return []
    requirement_text = rows[0]["requirement_text"]

    context = get_repo_context()

    prompt = f"""
    Requirement ID: {req.req_id}
    Requirement: {requirement_text}

    Source code context (Java classes):
    {context}

    Task:
    Generate 2–3 concrete test cases that validate this requirement.
    - Reference actual classes/methods from the repo where relevant.
    - Respond ONLY with a JSON list of objects, each containing:
      test_case_id, title, description, steps, expected_results.
    """

    response = model.generate_content(prompt)
    answer_text = ""
    for candidate in response.candidates:
        for part in candidate.content.parts:
            if hasattr(part, "text"):
                answer_text += part.text

    test_cases = safe_parse_json(answer_text, requirement_text, req.req_id)

    table_ref = "insulin-tcg-mcp.qa_metrics.test_cases"
    bq.insert_rows_json(table_ref, test_cases)
    return test_cases

@app.get("/")
def root():
    return {"message": "MCP server is running"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)

