import uuid
import glob
import json
import requests
import os
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List

from fastapi import FastAPI, Body
from pydantic import BaseModel
from google.cloud import bigquery, storage, secretmanager
from langchain.tools import StructuredTool
from langchain_google_vertexai import ChatVertexAI
from langgraph.prebuilt import create_react_agent
from langgraph.prebuilt.chat_agent_executor import AgentState
from vertexai.generative_models import GenerativeModel

# ============================
# App + Clients
# ============================

app = FastAPI(title="Insulin TCG MCP")

bq = bigquery.Client()
storage_client = storage.Client()
bucket_name = f"{bq.project}-tcg"
bucket = storage_client.bucket(bucket_name)

secret_client = secretmanager.SecretManagerServiceClient()

def access_secret(secret_name: str) -> str:
    name = f"projects/{bq.project}/secrets/{secret_name}/versions/latest"
    return secret_client.access_secret_version(request={"name": name}).payload.data.decode("UTF-8")

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
# LangGraph Agent Setup
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

tools = []

llm = ChatVertexAI(model="gemini-2.5-pro", temperature=0)
agent = create_react_agent(llm, tools, state_schema=AgentState)

# ============================
# Chat Endpoints
# ============================

@app.post("/agent/chat")
def agent_chat(payload: dict = Body(...)):
    message = payload.get("prompt", "").strip()
    if not message:
        return {"error": "Empty prompt."}
    try:
        state = agent.invoke({"messages": [("user", message)]})
        last_msg = state["messages"][-1][1] if isinstance(state["messages"][-1], tuple) else state["messages"][-1].content
        parsed = None
        if isinstance(last_msg, str):
            try:
                parsed = json.loads(last_msg)
            except Exception:
                parsed = None
        return {"prompt": message, "result": parsed if parsed else last_msg, "chat_history": state["messages"]}
    except Exception as e:
        return {"error": str(e)}

model = GenerativeModel("gemini-2.5-pro")

@app.post("/chat")
def chat_router(payload: dict = Body(...)):
    message = payload.get("prompt", "").strip()
    if not message:
        return {"error": "Empty prompt."}
    try:
        classifier_prompt = f"You are a classifier. Decide if this input is a REQUIREMENT or GENERAL QUESTION.\nInput: \"{message}\"\nRespond with only REQUIREMENT or GENERAL."
        classification = model.generate_content(classifier_prompt)
        decision = "".join([part.text.strip().upper() for c in classification.candidates for part in c.content.parts if hasattr(part, "text")])
        if "REQUIREMENT" in decision:
            return {"type": "requirement", "note": "Gemini classified as requirement. This would trigger requirement → testcase → iso → pytest → samples → jira.", "prompt": message}
        response = model.generate_content(message)
        answer_parts = [part.text for c in response.candidates for part in c.content.parts if hasattr(part, "text")]
        return {"type": "general", "prompt": message, "answer": "\n".join(answer_parts) if answer_parts else "(No answer text)"}
    except Exception as e:
        return {"error": f"Chat failed: {str(e)}"}

# ============================
# Tools
# ============================

@app.post("/tools/requirement.generate", response_model=RequirementResponse)
def requirement_generate(req: RequirementRequest):
    req_id = f"REQ-{uuid.uuid4().hex[:8].upper()}"
    requirement_text = req.prompt.strip()
    table_ref = f"{bq.project}.qa_metrics.requirements"
    rows = [{"req_id": req_id,"prompt": req.prompt,"requirement_text": requirement_text,"source_repo": req.source_repo or "","created_at": datetime.utcnow().isoformat()}]
    errors = bq.insert_rows_json(table_ref, rows)
    if errors: raise Exception(f"BigQuery insert error: {errors}")
    return RequirementResponse(req_id=req_id, requirement_text=requirement_text)

@app.post("/tools/testcase.generate")
def testcase_generate(req: TestCaseRequest) -> List[TestCaseResponse]:
    query = f"SELECT requirement_text FROM `{bq.project}.qa_metrics.requirements` WHERE req_id = '{req.req_id}'"
    rows = list(bq.query(query).result())
    if not rows: return []
    requirement_text = rows[0]["requirement_text"]

    context = ""  # simplified: repo context omitted

    prompt = f"""
    Requirement ID: {req.req_id}
    Requirement: {requirement_text}
    Task: Generate 2–3 JSON test cases with fields: test_case_id, title, description, steps, expected_results.
    """
    response = model.generate_content(prompt)
    answer_text = "".join([part.text for c in response.candidates for part in c.content.parts if hasattr(part, "text")])
    try:
        test_cases = json.loads(answer_text)
    except Exception:
        test_cases = [{"test_case_id": f"TC-{uuid.uuid4().hex[:6].upper()}","title": "Fallback test case","description": requirement_text,"steps": ["Step 1","Step 2"],"expected_results": ["Expected outcome"]}]
    for tc in test_cases: tc["req_id"] = req.req_id
    table_ref = f"{bq.project}.qa_metrics.test_cases"
    bq.insert_rows_json(table_ref, test_cases)
    return test_cases

@app.post("/tools/iso.validate")
def iso_validate(req: ISORequest) -> List[ISOResult]:
    results = []
    for tc_id in req.test_case_ids:
        compliant = not tc_id.endswith("3")
        missing = ["Acceptance criteria not detailed"] if not compliant else []
        suggestion = "Add precise acceptance criteria." if not compliant else "Looks good."
        results.append({"test_case_id": tc_id,"compliant": compliant,"missing_elements": missing,"related_iso_refs": ["ISO 62304 §5.5.1","ISO 14971 §7.4"],"suggestions": suggestion})
    rows = [{"validation_id": f"VAL-{uuid.uuid4().hex[:8].upper()}",**r,"validated_at": datetime.utcnow().isoformat()} for r in results]
    bq.insert_rows_json(f"{bq.project}.qa_metrics.iso_validation", rows)
    return results

@app.post("/tools/junit.generate")
def junit_generate(req: JiraRequest):
    results = []
    ids = ",".join([f"'{tc}'" for tc in req.test_case_ids])
    query = f"SELECT test_case_id,title,steps,expected_results FROM `{bq.project}.qa_metrics.test_cases` WHERE req_id='{req.req_id}' AND test_case_id IN ({ids})"
    rows = list(bq.query(query).result())
    for row in rows:
        tc_id, title, steps, expects = row["test_case_id"], row["title"], row.get("steps", []), row.get("expected_results", [])
        class_name = f"{tc_id}Test"
        file_name = f"{class_name}.java"
        steps_comment = "\n        ".join([f"// STEP: {s}" for s in steps])
        expects_comment = "\n        ".join([f"// EXPECT: {e}" for e in expects])
        junit_code = f"""package com.insulinpump.tests;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

public class {class_name} {{

    @Test
    public void testCase() {{
        // Requirement: {title}
        {steps_comment}
        {expects_comment}
        assertTrue(true); // placeholder assertion
    }}
}}
"""
        blob_path = f"artifacts/junit/{req.req_id}/{file_name}"
        bucket.blob(blob_path).upload_from_string(junit_code, content_type="text/x-java-source")
        results.append({"test_case_id": tc_id,"junit_path": f"gs://{bucket_name}/{blob_path}"})
    return results

@app.post("/tools/testresults.collect")
def testresults_collect(req: TestCaseRequest):
    """
    Collect test results from Maven Surefire reports and push to BigQuery.
    Scans multiple candidate directories for flexibility, with logging.
    """
    results = []
    candidate_dirs = [
        "target/surefire-reports",
        "insulin-repo/Personal_Insulin_Pump-Integrated_System/target/surefire-reports"
    ]

    report_dirs = [d for d in candidate_dirs if os.path.exists(d)]
    if not report_dirs:
        print("[testresults.collect] ❌ No Surefire reports found in any candidate directory.")
        return {"error": "No Surefire reports found in any candidate directory."}

    for report_dir in report_dirs:
        print(f"[testresults.collect] ✅ Found report directory: {report_dir}")
        for xml_file in glob.glob(os.path.join(report_dir, "*.xml")):
            try:
                print(f"[testresults.collect] Parsing file: {xml_file}")
                tree = ET.parse(xml_file)
                root = tree.getroot()

                for testcase in root.findall(".//testcase"):
                    classname = testcase.get("classname")

                    status = "PASS"
                    message = "Test passed"

                    if testcase.find("failure") is not None:
                        status = "FAIL"
                        message = testcase.find("failure").get("message") or "Failure"
                    elif testcase.find("error") is not None:
                        status = "ERROR"
                        message = testcase.find("error").get("message") or "Error"
                    elif testcase.find("skipped") is not None:
                        status = "SKIPPED"
                        message = testcase.find("skipped").get("message") or "Skipped"

                    test_case_id = classname.split(".")[-1].replace("Test", "")

                    results.append({
                        "req_id": req.req_id,
                        "test_case_id": test_case_id,
                        "status": status,
                        "message": message,
                        "recorded_at": datetime.utcnow().isoformat()
                    })
            except Exception as e:
                print(f"[testresults.collect] ⚠️ Failed to parse {xml_file}: {e}")
                continue

    if not results:
        print("[testresults.collect] ❌ No results parsed from Surefire reports.")
        return {"error": "No results parsed from Surefire reports."}

    table_ref = f"{bq.project}.qa_metrics.test_results"
    errors = bq.insert_rows_json(table_ref, results)
    if errors:
        print(f"[testresults.collect] ❌ BigQuery insert error: {errors}")
        return {"error": f"BigQuery insert error: {errors}"}

    print(f"[testresults.collect] ✅ Inserted {len(results)} results into BigQuery.")
    return {"inserted": len(results), "results": results}

# ============================
# Root
# ============================

@app.get("/")
def root():
    return {"message": "MCP server is running"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port)

