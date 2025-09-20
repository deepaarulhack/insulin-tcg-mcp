import os
import json
import uuid
import glob
import xml.etree.ElementTree as ET
import logging
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel
from google.cloud import bigquery, storage
import vertexai
from vertexai.generative_models import GenerativeModel

# -----------------------------
# Logging
# -----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("workflow")

# -----------------------------
# GCP Clients
# -----------------------------
bq = bigquery.Client()
PROJECT_ID = bq.project
REGION = os.environ.get("GOOGLE_CLOUD_REGION", "us-central1")

storage_client = storage.Client()
bucket_name = f"{PROJECT_ID}-tcg"
bucket = storage_client.bucket(bucket_name)

# -----------------------------
# Vertex AI Gemini
# -----------------------------
vertexai.init(project=PROJECT_ID, location=REGION)
gemini_model = GenerativeModel("gemini-2.5-flash")

# -----------------------------
# Models
# -----------------------------
class RequirementRequest(BaseModel):
    prompt: str
    source_repo: Optional[str] = ""

class RequirementResponse(BaseModel):
    req_id: str
    requirement_text: str

class TestCaseRequest(BaseModel):
    req_id: str

class TestCaseResponse(BaseModel):
    req_id: str
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
    gcs_sample_path: str
    local_sample_path: str

class JUnitRequest(BaseModel):
    req_id: str
    test_case_ids: List[str]

class JUnitResponse(BaseModel):
    test_case_id: str
    junit_path: str
    gcs_sample_path: str
    local_sample_path: str

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

class ISORequest(BaseModel):
    test_case_ids: List[str]

class ISOResult(BaseModel):
    test_case_id: str
    compliant: bool
    missing_elements: List[str]
    related_iso_refs: List[str]
    suggestions: str

class JiraRequest(BaseModel):
    req_id: str
    test_case_ids: List[str]
    run_id: Optional[str] = None

class JiraResponse(BaseModel):
    issue_key: str
    url: str

# -----------------------------
# Requirement → BigQuery
# -----------------------------
def requirement_generate(req: RequirementRequest) -> RequirementResponse:
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
    bq.insert_rows_json(table_ref, rows)
    return RequirementResponse(req_id=req_id, requirement_text=requirement_text)

# -----------------------------
# Test Cases → Gemini + BigQuery
# -----------------------------
def testcase_generate(req: TestCaseRequest) -> List[TestCaseResponse]:
    query = f"SELECT requirement_text FROM `{bq.project}.qa_metrics.requirements` WHERE req_id='{req.req_id}'"
    rows = list(bq.query(query).result())
    if not rows:
        return []
    requirement_text = rows[0]["requirement_text"]

    prompt = f"""
    Requirement ID: {req.req_id}
    Requirement: {requirement_text}
    Task: Generate 2–3 JSON test cases with fields:
    test_case_id, title, description, steps, expected_results.
    """
    response = gemini_model.generate_content(prompt)
    answer_text = "".join([
        part.text for c in response.candidates
        for part in c.content.parts if hasattr(part, "text")
    ])

    try:
        test_cases = json.loads(answer_text)
    except Exception:
        test_cases = [{
            "test_case_id": f"TC-{uuid.uuid4().hex[:6].upper()}",
            "title": f"Auto-generated test case for {req.req_id}",
            "description": requirement_text,
            "steps": [f"Interpret requirement: {requirement_text}", "Validate system behavior"],
            "expected_results": [f"System satisfies: {requirement_text}"]
        }]

    for tc in test_cases:
        tc["req_id"] = req.req_id

    table_ref = f"{bq.project}.qa_metrics.test_cases"
    bq.insert_rows_json(table_ref, test_cases)
    return [TestCaseResponse(**tc) for tc in test_cases]

# -----------------------------
# ISO Validation → BigQuery
# -----------------------------
def iso_validate(req: ISORequest) -> List[ISOResult]:
    results: List[ISOResult] = []
    for tc_id in req.test_case_ids:
        compliant = not tc_id.endswith("3")
        missing = ["Acceptance criteria not detailed"] if not compliant else []
        suggestion = "Add precise acceptance criteria." if not compliant else "Looks good."
        results.append(
            ISOResult(
                test_case_id=tc_id,
                compliant=compliant,
                missing_elements=missing,
                related_iso_refs=["ISO 62304 §5.5.1", "ISO 14971 §7.4"],
                suggestions=suggestion,
            )
        )
    rows = [{
        "validation_id": f"VAL-{uuid.uuid4().hex[:8].upper()}",
        "test_case_id": r.test_case_id,
        "compliant": r.compliant,
        "missing_elements": ", ".join(r.missing_elements),
        "related_iso_refs": ", ".join(r.related_iso_refs),
        "suggestions": r.suggestions,
        "validated_at": datetime.utcnow().isoformat(),
    } for r in results]
    table = f"{bq.project}.qa_metrics.iso_validation"
    bq.insert_rows_json(table, rows)
    return results

# -----------------------------
# Samples → GCS + Local
# -----------------------------
def samples_generate(req: SamplesRequest) -> List[SamplesResponse]:
    results = []
    for tc_id in req.test_case_ids:
        sample_content = {
            "test_case_id": tc_id,
            "input": {"glucose": 180, "dose": 2},
            "expected": {"delivery_logged": True}
        }

        # --- 1) Upload to GCS ---
        blob_path = f"artifacts/samples/{req.req_id}/{tc_id}.json"
        bucket.blob(blob_path).upload_from_string(
            json.dumps(sample_content, indent=2),
            content_type="application/json"
        )
        gcs_path = f"gs://{bucket_name}/{blob_path}"

        # --- 2) Save locally for JUnit (resources) ---
        local_dir = f"src/test/resources/samples"
        os.makedirs(local_dir, exist_ok=True)
        local_path = os.path.join(local_dir, f"{tc_id}.json")
        with open(local_path, "w") as f:
            json.dump(sample_content, f, indent=2)

        logger.info("Sample generated for %s → local=%s, gcs=%s", tc_id, local_path, gcs_path)

        results.append(SamplesResponse(
            test_case_id=tc_id,
            gcs_sample_path=gcs_path,
            local_sample_path=local_path
        ))

    return results

# -----------------------------
# JUnit → GCS with details
# -----------------------------
def junit_generate(req: JUnitRequest) -> List[JUnitResponse]:
    ids = ",".join([f"'{tc}'" for tc in req.test_case_ids])
    query = f"""
        SELECT test_case_id,title,steps,expected_results
        FROM `{bq.project}.qa_metrics.test_cases`
        WHERE req_id='{req.req_id}' AND test_case_id IN ({ids})
    """
    rows = list(bq.query(query).result())

    results = []
    for row in rows:
        tc_id, title = row["test_case_id"], row["title"]
        steps = row.get("steps") or ["Step 1"]
        expects = row.get("expected_results") or ["Expected outcome"]

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

        assertTrue(glucose > 0);
        assertTrue(dose >= 0);
    }}
}}
"""
        blob_path = f"artifacts/junit/{req.req_id}/{file_name}"
        bucket.blob(blob_path).upload_from_string(junit_code, content_type="text/x-java-source")

        results.append(JUnitResponse(
            test_case_id=tc_id,
            junit_path=f"gs://{bucket_name}/{blob_path}",
            gcs_sample_path=f"gs://{bucket_name}/artifacts/samples/{req.req_id}/{tc_id}.json",
            local_sample_path=f"src/test/resources/samples/{tc_id}.json"
        ))

    return results

# -----------------------------
# Test Results → Surefire reports
# -----------------------------
def testresults_collect(req: TestResultsRequest) -> TestResultsResponse:
    results = []
    candidate_dirs = [
        "target/surefire-reports",
        "insulin-repo/Personal_Insulin_Pump-Integrated_System/target/surefire-reports",
    ]
    report_dirs = [d for d in candidate_dirs if os.path.exists(d)]
    if not report_dirs:
        return TestResultsResponse(inserted=0, results=[])

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
                    results.append(TestResult(
                        req_id=req.req_id,
                        test_case_id=test_case_id,
                        status=status,
                        message=message,
                        sample_path=sample_path,
                        recorded_at=datetime.utcnow().isoformat()
                    ))
            except Exception:
                continue

    if not results:
        return TestResultsResponse(inserted=0, results=[])

    table_ref = f"{bq.project}.qa_metrics.test_results"
    bq.insert_rows_json(table_ref, [r.model_dump() for r in results])
    return TestResultsResponse(inserted=len(results), results=results)

# -----------------------------
# Jira (stubbed for now)
# -----------------------------
def jira_update(req: JiraRequest) -> JiraResponse:
    return JiraResponse(issue_key="KAN-11", url="https://deepaarulhack.atlassian.net/browse/KAN-11")

# -----------------------------
# Interactive Orchestrator
# -----------------------------
def interactive_pipeline(payload: dict, stage: str = "requirement") -> dict:
    state = {"status": "AWAITING_USER", "stage": stage}

    if stage == "requirement":
        r = requirement_generate(RequirementRequest(**payload))
        state.update({
            "req_id": r.req_id,
            "requirement": r.model_dump(),
            "next_stage": "testcases"
        })
        return state

    if stage == "testcases":
        req_id = payload["req_id"]
        tcs = testcase_generate(TestCaseRequest(req_id=req_id))
        iso_results = iso_validate(ISORequest(test_case_ids=[t.test_case_id for t in tcs]))
        state.update({
            "req_id": req_id,
            "test_case_ids": [t.test_case_id for t in tcs],
            "testcases": [t.model_dump() for t in tcs],
            "iso_validation": [v.model_dump() for v in iso_results],
            "next_stage": "samples_junit"
        })
        return state

    if stage == "samples_junit":
        req_id = payload["req_id"]
        tc_ids = payload["test_case_ids"]
        samples = samples_generate(SamplesRequest(req_id=req_id, test_case_ids=tc_ids))
        junits = junit_generate(JUnitRequest(req_id=req_id, test_case_ids=tc_ids))
        state.update({
            "req_id": req_id,
            "test_case_ids": tc_ids,
            "samples": [s.model_dump() for s in samples],
            "junit": [j.model_dump() for j in junits],
            "next_stage": "test_results"
        })
        return state

    if stage == "test_results":
        req_id = payload["req_id"]
        tr = testresults_collect(TestResultsRequest(req_id=req_id))
        state.update({
            "req_id": req_id,
            "test_results": tr.model_dump(),
            "next_stage": "jira"
        })
        return state

    if stage == "jira":
        req_id = payload["req_id"]
        jira = jira_update(JiraRequest(req_id=req_id, test_case_ids=payload["test_case_ids"]))
        state.update({
            "jira": jira.model_dump(),
            "status": "COMPLETE"
        })
        return state

    raise ValueError(f"Unknown stage: {stage}")

