import logging
from fastapi import FastAPI, HTTPException
from typing import Dict

from workflow import (
    RequirementRequest,
    RequirementResponse,
    TestCaseRequest,
    TestCaseResponse,
    SamplesRequest,
    SamplesResponse,
    JUnitRequest,
    JUnitResponse,
    TestResultsRequest,
    TestResultsResponse,
    ISORequest,
    ISOResult,
    JiraRequest,
    JiraResponse,
    requirement_generate,
    testcase_generate,
    samples_generate,
    junit_generate,
    testresults_collect,
    iso_validate,
    jira_update,
    interactive_pipeline,
)
from manager import manager_agent

# -----------------------------
# Logging
# -----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("server")

# -----------------------------
# FastAPI app
# -----------------------------
app = FastAPI(title="Insulin MCP Server", version="1.0")

# -----------------------------
# Health check
# -----------------------------
@app.get("/healthz")
def healthz():
    return {"status": "ok"}

# -----------------------------
# Manager Agent Endpoint
# -----------------------------
@app.post("/manager")
def manager(payload: Dict):
    """
    Manager agent:
      - If general → Gemini answers directly
      - If requirement → run pipeline (HITL: starts at requirement stage)
    """
    return manager_agent(payload)

# -----------------------------
# Pipeline: Start
# -----------------------------
@app.post("/pipeline/start")
def pipeline_start(payload: dict):
    """
    Start the pipeline with a new requirement.
    Returns requirement stage + req_id.
    """
    return interactive_pipeline(payload, stage="requirement")

# -----------------------------
# Pipeline: Continue
# -----------------------------
@app.post("/pipeline/continue")
def pipeline_continue(payload: dict):
    """
    Continue pipeline from current stage.
    Expects:
      - stage: str
      - req_id: str
      - test_case_ids: list[str] (only needed for samples/junit and jira stages)
      - user_action: "continue" or "stop"
    """
    stage = payload.get("stage")
    if not stage:
        return {"status": "ERROR", "error": "Missing 'stage' in request"}
    return interactive_pipeline(payload, stage=stage)

# -----------------------------
# Tools Endpoints
# -----------------------------
@app.post("/tools/requirement.generate", response_model=RequirementResponse)
def requirement_generate_tool(req: RequirementRequest):
    return requirement_generate(req)

@app.post("/tools/testcase.generate")
def testcase_generate_tool(req: TestCaseRequest) -> Dict:
    tcs = testcase_generate(req)
    return {"testcases": [t.model_dump() for t in tcs]}

@app.post("/tools/iso.validate")
def iso_validate_tool(req: ISORequest) -> Dict:
    results = iso_validate(req)
    return {"iso_validation": [r.model_dump() for r in results]}

@app.post("/tools/samples.generate")
def samples_generate_tool(req: SamplesRequest) -> Dict:
    samples = samples_generate(req)
    return {"samples": [s.model_dump() for s in samples]}

@app.post("/tools/junit.generate")
def junit_generate_tool(req: JUnitRequest) -> Dict:
    junits = junit_generate(req)
    return {"junit": [j.model_dump() for j in junits]}

@app.post("/tools/testresults.collect", response_model=TestResultsResponse)
def testresults_collect_tool(req: TestResultsRequest):
    return testresults_collect(req)

@app.post("/tools/jira.update", response_model=JiraResponse)
def jira_update_tool(req: JiraRequest):
    return jira_update(req)

