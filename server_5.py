import logging
from fastapi import FastAPI, HTTPException
from typing import Dict

from workflow import (
    # Models
    RequirementRequest, RequirementResponse,
    TestCaseRequest, TestCaseResponse,
    SamplesRequest, SamplesResponse,
    JUnitRequest, JUnitResponse,
    TestResultsRequest, TestResultsResponse,
    ISORequest, ISOResult,
    JiraRequest, JiraResponse,
    # Functions
    requirement_generate,
    testcase_generate,
    samples_generate,
    junit_generate,
    testresults_collect,
    iso_validate,
    jira_update,
    interactive_pipeline,
)

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
app = FastAPI(title="Insulin MCP Pipeline")

# -----------------------------
# Health & Debug
# -----------------------------
@app.get("/healthz")
def health():
    return {"status": "ok"}

@app.get("/check-gemini")
def check_gemini():
    # quick probe
    try:
        resp = {"gemini": "configured"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return resp

# -----------------------------
# Tools Endpoints
# -----------------------------
@app.post("/tools/requirement.generate", response_model=RequirementResponse)
def tool_requirement(req: RequirementRequest):
    return requirement_generate(req)

@app.post("/tools/testcase.generate", response_model=list[TestCaseResponse])
def tool_testcase(req: TestCaseRequest):
    return testcase_generate(req)

@app.post("/tools/iso.validate", response_model=list[ISOResult])
def tool_iso(req: ISORequest):
    return iso_validate(req)

@app.post("/tools/samples.generate", response_model=list[SamplesResponse])
def tool_samples(req: SamplesRequest):
    return samples_generate(req)

@app.post("/tools/junit.generate", response_model=list[JUnitResponse])
def tool_junit(req: JUnitRequest):
    return junit_generate(req)

@app.post("/tools/testresults.collect", response_model=TestResultsResponse)
def tool_testresults(req: TestResultsRequest):
    return testresults_collect(req)

@app.post("/tools/jira.update", response_model=JiraResponse)
def tool_jira(req: JiraRequest):
    return jira_update(req)

# -----------------------------
# Interactive Pipeline
# -----------------------------
@app.post("/pipeline/start")
def pipeline_start(payload: Dict):
    try:
        return interactive_pipeline(payload, stage="requirement")
    except Exception as e:
        logger.exception("Pipeline start failed")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/pipeline/continue")
def pipeline_continue(payload: Dict):
    try:
        stage = payload.get("stage")
        if not stage:
            raise HTTPException(status_code=400, detail="Missing stage in payload")
        return interactive_pipeline(payload, stage=stage)
    except Exception as e:
        logger.exception("Pipeline continue failed")
        raise HTTPException(status_code=500, detail=str(e))




from manager import manager_agent

@app.post("/manager")
def manager_route(payload: Dict):
    return manager_agent(payload)

