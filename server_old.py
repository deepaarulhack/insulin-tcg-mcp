import os
import json
import glob
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List, Optional

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from google.cloud import bigquery, storage, secretmanager

# --------------------------
# Logging Setup
# --------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("mcp-server")

# --------------------------
# FastAPI app
# --------------------------
app = FastAPI(title="Insulin TCG MCP")

# --------------------------
# GCP Clients
# --------------------------
bq = bigquery.Client()
storage_client = storage.Client()
bucket_name = f"{bq.project}-tcg"
bucket = storage_client.bucket(bucket_name)

secret_client = secretmanager.SecretManagerServiceClient()

def access_secret(secret_name: str) -> str:
    """Fetch latest secret value from Secret Manager"""
    try:
        name = f"projects/{bq.project}/secrets/{secret_name}/versions/latest"
        return secret_client.access_secret_version(
            request={"name": name}
        ).payload.data.decode("UTF-8")
    except Exception as e:
        logger.error(f"Failed to access secret {secret_name}: {e}")
        return ""

# --------------------------
# Jira Config
# --------------------------
JIRA_USER = access_secret("jira-user")
JIRA_TOKEN = access_secret("jira-token")
JIRA_URL = access_secret("jira-url")
JIRA_PROJECT_KEY = "KAN"   # Hardcoded to Geminators project

# --------------------------
# Request / Response Models
# --------------------------
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

# --------------------------
# Endpoints
# --------------------------
@app.get("/")
def root():
    logger.info("Health check called")
    return {"status": "ok", "service": "mcp-server"}

# =================================
# Requirement → BigQuery
# =================================
@app.post("/tools/requirement.generate", response_model=RequirementResponse)
def requirement_generate(req: RequirementRequest):
    logger.info(f"Generating requirement for prompt: {req.prompt}")
    try:
        req_id = f"REQ-{os.urandom(4).hex().upper()}"
        row = {
            "req_id": req_id,
            "prompt": req.prompt,
            "requirement_text": req.prompt,
            "source_repo": "github.com/myrepo",
            "created_at": datetime.utcnow().isoformat()
        }
        table_ref = f"{bq.project}.qa_metrics.requirements"
        errors = bq.insert_rows_json(table_ref, [row])
        if errors:
            logger.error(f"BigQuery insert error: {errors}")
            raise HTTPException(status_code=500, detail=f"BQ error: {errors}")
        logger.info(f"Requirement stored: {req_id}")
        return RequirementResponse(req_id=req_id, requirement_text=req.prompt)
    except Exception as e:
        logger.exception("Error in requirement_generate")
        raise HTTPException(status_code=500, detail=str(e))

# =================================
# Test Case → BigQuery
# =================================
@app.post("/tools/testcase.generate", response_model=List[TestCaseResponse])
def testcase_generate(req: TestCaseRequest):
    logger.info(f"Generating test case for req_id: {req.req_id}")
    try:
        tc_id = f"TC-{os.urandom(3).hex().upper()}"
        row = {
            "req_id": req.req_id,
            "test_case_id": tc_id,
            "title": f"Auto-generated test case for {req.req_id}",
            "description": "The pump shall log insulin delivery events.",
            "steps": ["Interpret requirement", "Validate system behavior matches requirement"],
            "expected_results": ["System satisfies requirement"]
        }
        table_ref = f"{bq.project}.qa_metrics.test_cases"
        errors = bq.insert_rows_json(table_ref, [row])
        if errors:
            logger.error(f"BigQuery insert error: {errors}")
            raise HTTPException(status_code=500, detail=f"BQ error: {errors}")
        logger.info(f"Test case generated: {tc_id}")
        return [row]
    except Exception as e:
        logger.exception("Error in testcase_generate")
        raise HTTPException(status_code=500, detail=str(e))

# =================================
# Samples → GCS
# =================================
@app.post("/tools/samples.generate", response_model=List[SamplesResponse])
def samples_generate(req: SamplesRequest):
    logger.info(f"Generating samples for req_id={req.req_id}, tcs={req.test_case_ids}")
    responses = []
    try:
        for tc in req.test_case_ids:
            obj = {
                "test_case_id": tc,
                "input": {"glucose": 180, "dose": 2},
                "expected": {"delivery_logged": True}
            }
            path = f"artifacts/samples/{req.req_id}/{tc}.json"
            blob = bucket.blob(path)
            blob.upload_from_string(json.dumps(obj, indent=2), content_type="application/json")
            responses.append(SamplesResponse(test_case_id=tc, sample_path=f"gs://{bucket_name}/{path}"))
            logger.info(f"Sample uploaded: gs://{bucket_name}/{path}")
        return responses
    except Exception as e:
        logger.exception("Error in samples_generate")
        raise HTTPException(status_code=500, detail=str(e))

# =================================
# JUnit → GCS
# =================================
@app.post("/tools/junit.generate", response_model=List[JUnitResponse])
def junit_generate(req: JUnitRequest):
    logger.info(f"Generating JUnit tests for req_id={req.req_id}")
    responses = []
    try:
        for tc in req.test_case_ids:
            class_name = tc.replace("-", "_") + "Test"
            java_code = f"""
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
import java.nio.file.*;
import org.json.JSONObject;

public class {class_name} {{
    @Test
    public void testCase() throws Exception {{
        String json = new String(Files.readAllBytes(Paths.get("src/test/resources/samples/{tc}.json")));
        JSONObject sample = new JSONObject(json);
        int glucose = sample.getJSONObject("input").getInt("glucose");
        int dose = sample.getJSONObject("input").getInt("dose");
        assertTrue(glucose > 0);
        assertTrue(dose > 0);
    }}
}}
"""
            path = f"artifacts/junit/{req.req_id}/{class_name}.java"
            blob = bucket.blob(path)
            blob.upload_from_string(java_code, content_type="text/x-java-source")
            responses.append(JUnitResponse(
                test_case_id=tc,
                junit_path=f"gs://{bucket_name}/{path}",
                sample_path=f"gs://{bucket_name}/artifacts/samples/{req.req_id}/{tc}.json"
            ))
            logger.info(f"JUnit uploaded: gs://{bucket_name}/{path}")
        return responses
    except Exception as e:
        logger.exception("Error in junit_generate")
        raise HTTPException(status_code=500, detail=str(e))

# =================================
# Collect Test Results → BigQuery
# =================================
@app.post("/tools/testresults.collect", response_model=TestResultsResponse)
def testresults_collect(req: TestResultsRequest):
    logger.info(f"Collecting test results for req_id={req.req_id}")
    results = []
    try:
        report_dirs = [
            "target/surefire-reports",
            "insulin-repo/Personal_Insulin_Pump-Integrated_System/target/surefire-reports"
        ]
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
                        logger.info(f"Parsed result: {test_case_id}={status}")
                except Exception as parse_err:
                    logger.warning(f"Failed to parse {xml_file}: {parse_err}")
                    continue
        if not results:
            logger.warning("No results parsed from Surefire reports")
            return TestResultsResponse(inserted=0, results=[])
        table_ref = f"{bq.project}.qa_metrics.test_results"
        errors = bq.insert_rows_json(table_ref, results)
        if errors:
            logger.error(f"BigQuery insert error: {errors}")
            raise HTTPException(status_code=500, detail=f"BQ error: {errors}")
        logger.info(f"Inserted {len(results)} test results")
        return TestResultsResponse(inserted=len(results), results=results)
    except Exception as e:
        logger.exception("Error in testresults_collect")
        raise HTTPException(status_code=500, detail=str(e))

# =================================
# Jira Update (API v3 + KAN + ADF + FIXED search)
# =================================
@app.post("/tools/jira.update", response_model=JiraResponse)
def jira_update(req: JiraRequest):
    logger.info(f"Updating Jira for req_id={req.req_id}, run_id={req.run_id}")
    try:
        run_id = req.run_id or f"run-{int(datetime.utcnow().timestamp())}"

        # --- Search Jira using new endpoint ---
        search_payload = {
            "jql": f'project = {JIRA_PROJECT_KEY} AND summary ~ "{req.req_id}"'
        }
        logger.info(f"Jira search payload: {search_payload}")

        search_resp = requests.post(
            f"{JIRA_URL}/rest/api/3/search/jql",
            auth=(JIRA_USER, JIRA_TOKEN),
            headers={"Content-Type": "application/json"},
            json=search_payload
        )
        if search_resp.status_code != 200:
            error_text = search_resp.text
            logger.error(f"Jira search failed: {search_resp.status_code}, {error_text}")
            raise HTTPException(status_code=500, detail=f"Jira search failed: {error_text}")

        search_json = search_resp.json()
        run_section = f"### Test Run {run_id} ({datetime.utcnow().isoformat()} UTC)\nAuto-update from MCP pipeline"

        if search_json.get("issues"):
            issue_key = search_json["issues"][0]["key"]
            logger.info(f"Found existing Jira issue {issue_key}, adding comment")
            comment_payload = {
                "body": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": run_section}
                            ]
                        }
                    ]
                }
            }
            comment_resp = requests.post(
                f"{JIRA_URL}/rest/api/3/issue/{issue_key}/comment",
                auth=(JIRA_USER, JIRA_TOKEN),
                headers={"Content-Type": "application/json"},
                json=comment_payload
            )
            if comment_resp.status_code >= 300:
                logger.error(f"Failed to add Jira comment: {comment_resp.status_code}, {comment_resp.text}")
                raise HTTPException(status_code=500, detail="Failed to add Jira comment")
            return JiraResponse(issue_key=issue_key, url=f"{JIRA_URL}/browse/{issue_key}")

        # --- Create new Jira issue ---
        logger.info(f"No Jira issue found, creating new one for req_id={req.req_id}")
        issue_payload = {
            "fields": {
                "project": {"key": JIRA_PROJECT_KEY},
                "summary": f"{req.req_id} - Test Cases & Results",
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": run_section}
                            ]
                        }
                    ]
                },
                "issuetype": {"name": "Task"}
            }
        }
        create_resp = requests.post(
            f"{JIRA_URL}/rest/api/3/issue",
            auth=(JIRA_USER, JIRA_TOKEN),
            headers={"Content-Type": "application/json"},
            json=issue_payload
        )
        create_json = create_resp.json()
        if create_resp.status_code >= 300 or "key" not in create_json:
            logger.error(f"Jira create failed: {create_resp.status_code}, {create_json}")
            raise HTTPException(status_code=500, detail=f"Jira create failed: {create_json}")

        issue_key = create_json["key"]
        logger.info(f"Created Jira issue {issue_key}")
        return JiraResponse(issue_key=issue_key, url=f"{JIRA_URL}/browse/{issue_key}")

    except Exception as e:
        logger.exception("Error in jira_update")
        raise HTTPException(status_code=500, detail=str(e))

# =================================
# Debug: Check Jira Config
# =================================
@app.get("/check-jira-config")
def check_jira_config():
    try:
        result = {
            "jira_user": JIRA_USER if JIRA_USER else "EMPTY",
            "jira_url": JIRA_URL if JIRA_URL else "EMPTY",
            "jira_project_key": JIRA_PROJECT_KEY,
            "jira_token_length": len(JIRA_TOKEN) if JIRA_TOKEN else 0,
        }
        if not JIRA_USER or not JIRA_TOKEN or not JIRA_URL:
            result["warning"] = "One or more Jira secrets are missing or not accessible!"
        return result
    except Exception as e:
        logger.exception("Error reading Jira secrets")
        raise HTTPException(status_code=500, detail=str(e))

# =================================
# Agentic Chat Endpoint
# =================================
@app.post("/chat")
def chat_entry(payload: dict):
    logger.info(f"/chat invoked with payload: {payload}")
    try:
        from workflow import chat_orchestrator  # lazy import avoids circular
        result = chat_orchestrator(payload)
        logger.info(f"/chat final_state keys: {list(result.keys())}")
        return result
    except Exception as e:
        logger.exception("Error in /chat endpoint")
        raise HTTPException(status_code=500, detail=str(e))

