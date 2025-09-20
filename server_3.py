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
from google.cloud import bigquery, storage

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
app = FastAPI()

# --------------------------
# Config
# --------------------------
PROJECT_ID = os.getenv("PROJECT_ID", "insulin-tcg-mcp")
BUCKET_NAME = f"{PROJECT_ID}-tcg"
JIRA_URL = "https://deepaarulhack.atlassian.net"
JIRA_USER = os.getenv("JIRA_USER")
JIRA_TOKEN = os.getenv("JIRA_TOKEN")

bq = bigquery.Client(project=PROJECT_ID)
gcs = storage.Client(project=PROJECT_ID)
bucket = gcs.bucket(BUCKET_NAME)

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
        table_ref = f"{PROJECT_ID}.qa_metrics.requirements"
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
        table_ref = f"{PROJECT_ID}.qa_metrics.test_cases"
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
            responses.append(SamplesResponse(test_case_id=tc, sample_path=f"gs://{BUCKET_NAME}/{path}"))
            logger.info(f"Sample uploaded: gs://{BUCKET_NAME}/{path}")
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
                junit_path=f"gs://{BUCKET_NAME}/{path}",
                sample_path=f"gs://{BUCKET_NAME}/artifacts/samples/{req.req_id}/{tc}.json"
            ))
            logger.info(f"JUnit uploaded: gs://{BUCKET_NAME}/{path}")
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
                        sample_path = f"gs://{BUCKET_NAME}/artifacts/samples/{req.req_id}/{test_case_id}.json"
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
        table_ref = f"{PROJECT_ID}.qa_metrics.test_results"
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
# Jira Update (patched)
# =================================
@app.post("/tools/jira.update", response_model=JiraResponse)
def jira_update(req: JiraRequest):
    logger.info(f"Updating Jira for req_id={req.req_id}, run_id={req.run_id}")
    try:
        ids = ",".join([f"'{tc}'" for tc in req.test_case_ids])

        query = f"""
            SELECT test_case_id, status, sample_path, recorded_at
            FROM `{PROJECT_ID}.qa_metrics.test_results`
            WHERE req_id='{req.req_id}'
              AND test_case_id IN ({ids})
            ORDER BY recorded_at DESC
            LIMIT 20
        """
        rows = list(bq.query(query).result())
        run_id = req.run_id or f"run-{int(datetime.utcnow().timestamp())}"

        run_lines = [f"### Test Run {run_id} ({datetime.utcnow().isoformat()} UTC)"]
        if not rows:
            run_lines.append("(No test results found in BigQuery)")
        else:
            for r in rows:
                run_lines.append(
                    f"- {r['test_case_id']}: {r['status']} "
                    f"(Sample: {r['sample_path']}, Recorded: {r['recorded_at']})"
                )
        run_section = "\n".join(run_lines)

        # --- Search Jira ---
        search_resp = requests.get(
            f"{JIRA_URL}/rest/api/2/search",
            auth=(JIRA_USER, JIRA_TOKEN),
            params={"jql": f'project=KAN AND summary~"{req.req_id}"'}
        )
        if search_resp.status_code != 200:
            logger.error(f"Jira search failed: {search_resp.status_code}, {search_resp.text}")
            raise HTTPException(status_code=500, detail="Jira search failed")

        search_json = search_resp.json()
        if search_json.get("issues"):
            issue_key = search_json["issues"][0]["key"]
            logger.info(f"Found existing Jira issue {issue_key}, adding comment")
            comment_resp = requests.post(
                f"{JIRA_URL}/rest/api/2/issue/{issue_key}/comment",
                auth=(JIRA_USER, JIRA_TOKEN),
                json={"body": run_section}
            )
            if comment_resp.status_code >= 300:
                logger.error(f"Failed to add Jira comment: {comment_resp.status_code}, {comment_resp.text}")
                raise HTTPException(status_code=500, detail="Failed to add Jira comment")
            return JiraResponse(issue_key=issue_key, url=f"{JIRA_URL}/browse/{issue_key}")

        # --- Create new Jira issue ---
        logger.info(f"No Jira issue found, creating new one for req_id={req.req_id}")
        desc_lines = [f"*Requirement:* {req.req_id}\n"]
        tc_query = f"""
            SELECT test_case_id,title,description,steps,expected_results
            FROM `{PROJECT_ID}.qa_metrics.test_cases`
            WHERE req_id='{req.req_id}'
              AND test_case_id IN ({ids})
        """
        tc_rows = list(bq.query(tc_query).result())
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

        issue_payload = {
            "fields": {
                "project": {"key": "KAN"},
                "summary": f"{req.req_id} - Test Cases & Results",
                "description": "\n".join(desc_lines),
                "issuetype": {"name": "Task"}
            }
        }

        create_resp = requests.post(
            f"{JIRA_URL}/rest/api/2/issue",
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

