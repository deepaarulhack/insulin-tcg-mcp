import uuid
import os
import logging
from datetime import datetime
from typing import List, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Import orchestrator & manager
from workflow import interactive_pipeline
from manager import manager_agent

# ---------------------------------------------------
# FastAPI app
# ---------------------------------------------------
app = FastAPI(title="MCP Server", version="1.0.0")

# Enable CORS (for frontend → backend connectivity)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------
# Models
# ---------------------------------------------------
class RequirementRequest(BaseModel):
    prompt: str
    source_repo: str | None = None

class RequirementResponse(BaseModel):
    req_id: str
    requirement_text: str

class TestCaseRequest(BaseModel):
    req_id: str

class SamplesRequest(BaseModel):
    req_id: str
    test_case_ids: List[str]

class JUnitRequest(BaseModel):
    req_id: str
    test_case_ids: List[str]

class TestResultsRequest(BaseModel):
    req_id: str

class JiraRequest(BaseModel):
    req_id: str
    test_case_ids: List[str]
    run_id: str | None = None

class JiraResponse(BaseModel):
    issue_key: str
    url: str

class ISORequest(BaseModel):
    test_case_ids: List[str]

class ISOResult(BaseModel):
    test_case_id: str
    compliant: bool
    missing_elements: List[str]
    related_iso_refs: List[str]
    suggestions: str

# ---------------------------------------------------
# Health check
# ---------------------------------------------------
@app.get("/healthz")
def healthz():
    return {"status": "ok"}

# ---------------------------------------------------
# Manager Agent
# ---------------------------------------------------
@app.post("/manager")
def manager(payload: Dict[str, Any]):
    return manager_agent(payload)

# ---------------------------------------------------
# Pipeline endpoints
# ---------------------------------------------------
@app.post("/pipeline/start")
def pipeline_start(req: RequirementRequest):
    payload = {"prompt": req.prompt, "source_repo": req.source_repo}
    return interactive_pipeline(payload, stage="requirement")

@app.post("/pipeline/continue")
def pipeline_continue(payload: Dict[str, Any]):
    stage = payload.get("stage", "requirement")
    return interactive_pipeline(payload, stage=stage)

# ---------------------------------------------------
# ISO Validation (standalone endpoint)
# ---------------------------------------------------
@app.post("/tools/iso.validate", response_model=List[ISOResult])
def iso_validate(req: ISORequest):
    results = []
    for tc_id in req.test_case_ids:
        compliant = not tc_id.endswith("3")
        missing = ["Acceptance criteria not detailed"] if not compliant else []
        suggestion = "Add precise acceptance criteria." if not compliant else "Looks good."
        results.append({
            "test_case_id": tc_id,
            "compliant": compliant,
            "missing_elements": missing,
            "related_iso_refs": ["ISO 62304 §5.5.1", "ISO 14971 §7.4"],
            "suggestions": suggestion
        })
    return results

