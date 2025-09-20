





import requests
from langgraph.graph import StateGraph, END

from server import (
    requirement_generate,
    testcase_generate,
    samples_generate,
    junit_generate,
    testresults_collect,
    jira_update,
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
    JiraRequest,
    JiraResponse,
)

# -----------------------------
# Step Functions
# -----------------------------
def requirement_step(state):
    """Generate requirement ID + text"""
    req = RequirementRequest(prompt=state["prompt"])
    resp = requirement_generate(req)
    state["req_id"] = resp.req_id
    state["requirement_text"] = resp.requirement_text
    return state

def testcase_step(state):
    """Generate test cases for requirement"""
    req = TestCaseRequest(req_id=state["req_id"])
    resp = testcase_generate(req)
    state["test_cases"] = resp
    state["test_case_ids"] = [tc["test_case_id"] for tc in resp]
    return state

def samples_step(state):
    """Generate sample data"""
    req = SamplesRequest(
        req_id=state["req_id"],
        test_case_ids=state["test_case_ids"],
    )
    resp = samples_generate(req)
    state["samples"] = resp
    return state

def junit_step(state):
    """Generate JUnit test files"""
    req = JUnitRequest(
        req_id=state["req_id"],
        test_case_ids=state["test_case_ids"],
    )
    resp = junit_generate(req)
    state["junit"] = resp
    return state

def testresults_step(state):
    """Collect test results"""
    req = TestResultsRequest(req_id=state["req_id"])
    resp = testresults_collect(req)
    state["test_results"] = resp.get("results", [])
    return state

def jira_step(state):
    """Update Jira issue with results"""
    run_id = f"run-{state.get('req_id')}"
    req = JiraRequest(
        req_id=state["req_id"],
        test_case_ids=state["test_case_ids"],
        run_id=run_id,
    )
    resp = jira_update(req)
    state["jira"] = resp
    return state

# -----------------------------
# LangGraph Orchestration
# -----------------------------
def build_workflow():
    workflow = StateGraph(dict)

    workflow.add_node("requirement", requirement_step)
    workflow.add_node("testcase", testcase_step)
    workflow.add_node("samples", samples_step)
    workflow.add_node("junit", junit_step)
    workflow.add_node("testresults", testresults_step)
    workflow.add_node("jira", jira_step)

    workflow.set_entry_point("requirement")
    workflow.add_edge("requirement", "testcase")
    workflow.add_edge("testcase", "samples")
    workflow.add_edge("samples", "junit")
    workflow.add_edge("junit", "testresults")
    workflow.add_edge("testresults", "jira")
    workflow.add_edge("jira", END)

    return workflow.compile()

# -----------------------------
# Chat Entry for /chat
# -----------------------------
def chat_orchestrator(payload: dict):
    """Main entry for /chat requests"""
    workflow = build_workflow()
    state = {"prompt": payload["prompt"]}
    final_state = workflow.invoke(state)
    return final_state