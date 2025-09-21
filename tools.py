import os
import json
import glob
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List, Optional

import requests
from fastapi import HTTPException
from google.cloud import bigquery, storage, secretmanager

from models import (
    RequirementRequest,
    RequirementResponse,
    TestCaseRequest,
    TestCaseResponse,
    SamplesRequest,
    SamplesResponse,
    JUnitRequest,
    JUnitResponse,
    TestResultsRequest,
    TestResult,
    TestResultsResponse,
    JiraRequest,
    JiraResponse,
)

# --------------------------
# Logging Setup
# --------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("mcp-tools")

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
JIRA_USER = "deepaarulhack@gmail.com"
JIRA_TOKEN = ""
JIRA_URL = "https://deepaarulhack.atlassian.net"
JIRA_PROJECT_KEY = "KAN"
HTTP_TIMEOUT = 20  # seconds

# =================================
# Tool Functions
# =================================

def requirement_generate(req: RequirementRequest) -> RequirementResponse:
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

def testcase_generate(req: TestCaseRequest) -> List[TestCaseResponse]:
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
        return [TestCaseResponse(**row)]
    except Exception as e:
        logger.exception("Error in testcase_generate")
        raise HTTPException(status_code=500, detail=str(e))

def samples_generate(req: SamplesRequest) -> List[SamplesResponse]:
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

def junit_generate(req: JUnitRequest) -> List[JUnitResponse]:
    logger.info(f"Generating JUnit tests for req_id={req.req_id}")
    responses = []
    try:
        for tc in req.test_case_ids:
            class_name = tc.replace("-", "_") + "Test"
            java_code = f"""...""" # (omitted for brevity)
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

def testresults_collect(req: TestResultsRequest) -> TestResultsResponse:
    logger.info(f"Collecting test results for req_id={req.req_id}")
    results = []
    try:
        # ... (omitted for brevity)
        return TestResultsResponse(inserted=len(results), results=results)
    except Exception as e:
        logger.exception("Error in testresults_collect")
        raise HTTPException(status_code=500, detail=str(e))

def jira_update(req: JiraRequest) -> JiraResponse:
    logger.info(f"Updating Jira for req_id={req.req_id}, run_id={req.run_id}")
    try:
        # ... (omitted for brevity)
        return JiraResponse(issue_key=issue_key, url=f"{JIRA_URL}/browse/{issue_key}")
    except Exception as e:
        logger.exception("Error in jira_update")
        raise HTTPException(status_code=500, detail=str(e))

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
