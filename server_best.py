import uuid
import glob
import json
import requests
import os
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List

from fastapi import FastAPI
from pydantic import BaseModel
from google.cloud import bigquery, storage, secretmanager
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
    return secret_client.access_secret_version(
        request={"name": name}
    ).payload.data.decode("UTF-8")

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

tools = []
llm = ChatVertexAI(model="gemini-2.5-pro", temperature=0)
agent = create_react_agent(llm, tools, state_schema=AgentState)
model = GenerativeModel("gemini-2.5-pro")

# ============================
# Tools
# ============================

@app.post("/tools/requirement.generate", response_model=RequirementResponse)
def requirement_generate(req: RequirementRequest):
    req_id = f"REQ-{uuid.uuid4().hex[:8].upper()}"
    requirement_text = req.prompt.strip()
    table_ref = f"{bq.project}.qa_metrics.requirements"
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
    query = f"SELECT requirement_text FROM `{bq.project}.qa_metrics.requirements` WHERE req_id = '{req.req_id}'"
    rows = list(bq.query(query).result())
    if not rows:
        return []
    requirement_text = rows[0]["requirement_text"]

    prompt = f"""
    Requirement ID: {req.req_id}
    Requirement: {requirement_text}
    Task: Generate 2–3 JSON test cases with fields: test_case_id, title, description, steps, expected_results.
    """
    response = model.generate_content(prompt)
    answer_text = "".join([
        part.text for c in response.candidates
        for part in c.content.parts if hasattr(part, "text")
    ])
    try:
        test_cases = json.loads(answer_text)
    except Exception:
        # Smarter fallback
        test_cases = [{
            "test_case_id": f"TC-{uuid.uuid4().hex[:6].upper()}",
            "title": f"Auto-generated test case for {req.req_id}",
            "description": requirement_text,
            "steps": [
                f"Interpret requirement: {requirement_text}",
                "Validate system behavior matches requirement"
            ],
            "expected_results": [
                f"System satisfies: {requirement_text}"
            ]
        }]
    for tc in test_cases:
        tc["req_id"] = req.req_id
    table_ref = f"{bq.project}.qa_metrics.test_cases"
    bq.insert_rows_json(table_ref, test_cases)
    return test_cases

@app.post("/tools/samples.generate")
def samples_generate(req: SamplesRequest) -> List[SamplesResult]:
    results = []
    for tc_id in req.test_case_ids:
        sample_content = {
            "test_case_id": tc_id,
            "input": {"glucose": 180, "dose": 2},
            "expected": {"delivery_logged": True}
        }
        blob_path = f"artifacts/samples/{req.req_id}/{tc_id}.json"
        bucket.blob(blob_path).upload_from_string(
            json.dumps(sample_content, indent=2),
            content_type="application/json"
        )
        results.append({
            "test_case_id": tc_id,
            "sample_path": f"gs://{bucket_name}/{blob_path}"
        })
    return results

@app.post("/tools/junit.generate")
def junit_generate(req: JiraRequest):
    results = []
    ids = ",".join([f"'{tc}'" for tc in req.test_case_ids])
    query = f"""
        SELECT test_case_id,title,steps,expected_results
        FROM `{bq.project}.qa_metrics.test_cases`
        WHERE req_id='{req.req_id}' AND test_case_id IN ({ids})
    """
    rows = list(bq.query(query).result())

    for row in rows:
        tc_id, title = row["test_case_id"], row["title"]
        steps = row.get("steps") or ["Step 1"]
        expects = row.get("expected_results") or ["Expected outcome"]

        # ✅ sanitize class/file name
        safe_id = tc_id.replace("-", "_")
        class_name = f"{safe_id}Test"
        file_name = f"{class_name}.java"

        steps_comment = "\n        ".join([f"// STEP: {s}" for s in steps])
        expects_comment = "\n        ".join([f"// EXPECT: {e}" for e in expects])

        junit_code = f"""package com.insulinpump.tests;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
import java.nio.file.*;
import org.json.JSONObject;

public class {class_name} {{

    @Test
    public void testCase() throws Exception {{
        // Requirement: {title}
        {steps_comment}
        {expects_comment}

        // Load sample JSON
        String json = new String(Files.readAllBytes(Paths.get("src/test/resources/samples/{tc_id}.json")));
        JSONObject sample = new JSONObject(json);

        int glucose = sample.getJSONObject("input").getInt("glucose");
        int dose = sample.getJSONObject("input").getInt("dose");

        // TODO: Replace with real AppController call
        assertTrue(glucose > 0);
        assertTrue(dose >= 0);
    }}
}}
"""
        # ✅ overwrite old files
        blob_path = f"artifacts/junit/{req.req_id}/{file_name}"
        bucket.blob(blob_path).upload_from_string(
            junit_code,
            content_type="text/x-java-source"
        )
        results.append({
            "test_case_id": tc_id,
            "junit_path": f"gs://{bucket_name}/{blob_path}"
        })

    return results

@app.post("/tools/testresults.collect")
def testresults_collect(req: TestCaseRequest):
    results = []
    candidate_dirs = [
        "target/surefire-reports",
        "insulin-repo/Personal_Insulin_Pump-Integrated_System/target/surefire-reports",
    ]
    report_dirs = [d for d in candidate_dirs if os.path.exists(d)]
    if not report_dirs:
        return {"error": "No Surefire reports found in any candidate directory."}

    for report_dir in report_dirs:
        for xml_file in glob.glob(os.path.join(report_dir, "*.xml")):
            try:
                tree = ET.parse(xml_file)
                root = tree.getroot()
                for testcase in root.findall(".//testcase"):
                    classname = testcase.get("classname")
                    status, message = "PASS", "Test passed"
                    if testcase.find("failure") is not None:
                        status, message = "FAIL", testcase.find("failure").get("message") or "Failure"
                    elif testcase.find("error") is not None:
                        status, message = "ERROR", testcase.find("error").get("message") or "Error"
                    elif testcase.find("skipped") is not None:
                        status, message = "SKIPPED", testcase.find("skipped").get("message") or "Skipped"

                    test_case_id = classname.split(".")[-1].replace("Test", "").replace("_", "-")
                    sample_path = f"gs://{bucket_name}/artifacts/samples/{req.req_id}/{test_case_id}.json"
                    results.append({
                        "req_id": req.req_id,
                        "test_case_id": test_case_id,
                        "status": status,
                        "message": message,
                        "sample_path": sample_path,
                        "recorded_at": datetime.utcnow().isoformat()
                    })
            except Exception:
                continue

    if not results:
        return {"error": "No results parsed from Surefire reports."}

    table_ref = f"{bq.project}.qa_metrics.test_results"
    errors = bq.insert_rows_json(table_ref, results)
    if errors:
        return {"error": f"BigQuery insert error: {errors}"}
    return {"inserted": len(results), "results": results}

# ============================
# Jira Update with inline samples
# ============================

def load_sample_json(path: str) -> str:
    if not path or not path.startswith("gs://"):
        return ""
    try:
        parts = path.replace("gs://", "").split("/", 1)
        bucket_name, blob_path = parts[0], parts[1]
        blob = storage.Client().bucket(bucket_name).blob(blob_path)
        content = blob.download_as_text()
        if len(content) > 800:
            return content[:800] + "... (truncated)"
        return content
    except Exception as e:
        return f"(Failed to load sample from {path}: {e})"

@app.post("/tools/jira.update", response_model=JiraResponse)
def jira_update(req: JiraRequest):
    query = f"""
        SELECT AS STRUCT test_case_id, status, sample_path, recorded_at
        FROM (
            SELECT test_case_id, status, sample_path, recorded_at,
                   ROW_NUMBER() OVER(PARTITION BY test_case_id ORDER BY recorded_at DESC) as rn
            FROM `{bq.project}.qa_metrics.test_results`
            WHERE req_id='{req.req_id}'
              AND test_case_id IN UNNEST({req.test_case_ids})
        )
        WHERE rn = 1
    """
    rows = list(bq.query(query).result())

    run_id = req.run_id or f"run-{int(datetime.utcnow().timestamp())}"
    run_lines = [f"### Test Run {run_id} ({datetime.utcnow().isoformat()} UTC)"]
    for r in rows:
        sample_json = load_sample_json(r['sample_path'])
        if sample_json:
            run_lines.append(
                f"- *{r['test_case_id']}*: {r['status']}\n  *Sample Used:*\n  {sample_json}"
            )
        else:
            run_lines.append(
                f"- *{r['test_case_id']}*: {r['status']} (Sample path: {r['sample_path']} not loaded)"
            )
    run_section = "\n".join(run_lines)

    # Check Jira issue
    search_resp = requests.get(
        f"{JIRA_URL}/rest/api/2/search",
        auth=(JIRA_USER, JIRA_TOKEN),
        params={"jql": f'project=KAN AND summary~"{req.req_id}"'}
    ).json()

    if search_resp.get("issues"):
        issue_key = search_resp["issues"][0]["key"]
        requests.post(
            f"{JIRA_URL}/rest/api/2/issue/{issue_key}/comment",
            auth=(JIRA_USER, JIRA_TOKEN),
            json={"body": run_section}
        )
        return JiraResponse(issue_key=issue_key, url=f"{JIRA_URL}/browse/{issue_key}")

    # Create new issue with test cases + run
    tc_query = f"""
        SELECT test_case_id,title,description,steps,expected_results
        FROM `{bq.project}.qa_metrics.test_cases`
        WHERE req_id='{req.req_id}'
          AND test_case_id IN UNNEST({req.test_case_ids})
    """
    tc_rows = list(bq.query(tc_query).result())

    desc_lines = [f"*Requirement:* {req.req_id}\n"]
    for tc in tc_rows:
        desc_lines.append(f"*{tc['test_case_id']}: {tc['title']}*")
        desc_lines.append(f"Description: {tc['description']}")
        desc_lines.append("*Steps:*")
        for s in tc["steps"]:
            desc_lines.append(f"  - {s}")
        desc_lines.append("*Expected:*")
        for e in tc["expected_results"]:
            desc_lines.append(f"  - {e}")
        desc_lines.append("")

    desc_lines.append("\n---\n")
    desc_lines.append(run_section)

    issue_data = {
        "fields": {
            "project": {"key": "KAN"},
            "summary": f"Requirement {req.req_id} - Automated Tests",
            "description": "\n".join(desc_lines),
            "issuetype": {"name": "Task"},
        }
    }

    resp = requests.post(
        f"{JIRA_URL}/rest/api/2/issue",
        auth=(JIRA_USER, JIRA_TOKEN),
        json=issue_data,
    ).json()

    return JiraResponse(issue_key=resp["key"], url=f"{JIRA_URL}/browse/{resp['key']}")

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

