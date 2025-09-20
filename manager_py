import logging
from fastapi import HTTPException
import vertexai
from vertexai.generative_models import GenerativeModel
from workflow import interactive_pipeline
from google.cloud import bigquery

# -----------------------------
# Logging
# -----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("manager")

# -----------------------------
# Vertex AI Gemini setup
# -----------------------------
bq = bigquery.Client()
PROJECT_ID = bq.project
REGION = "us-central1"

vertexai.init(project=PROJECT_ID, location=REGION)
gemini_model = GenerativeModel("gemini-2.5-flash")

def call_gemini(prompt: str) -> str:
    try:
        response = gemini_model.generate_content(prompt)
        return "".join([
            part.text for c in response.candidates
            for part in c.content.parts if hasattr(part, "text")
        ])
    except Exception as e:
        logger.error(f"[Gemini Vertex AI Error] {e}")
        return f"[Gemini error: {e}]"

# -----------------------------
# Classification
# -----------------------------
def classify_prompt(prompt: str) -> str:
    classification_prompt = f"""
    You are a classifier. Classify the following user prompt strictly as either:
    - requirement (if it's a system/software requirement statement, e.g. 'The pump shall...')
    - general (if it's a normal question or casual conversation)

    Prompt: "{prompt}"

    Answer with only one word: requirement or general.
    """
    label = call_gemini(classification_prompt).strip().lower()
    logger.info(f"Classified prompt='{prompt}' → {label}")
    return "requirement" if "requirement" in label else "general"

# -----------------------------
# Manager Agent
# -----------------------------
def manager_agent(payload: dict):
    try:
        prompt = payload.get("prompt", "")
        if not prompt:
            return {"error": "No prompt provided"}

        label = classify_prompt(prompt)

        if label == "requirement":
            logger.info("Forwarding to pipeline orchestrator")
            result = interactive_pipeline(payload, stage="requirement")
        else:
            logger.info("Answering as general chatbot")
            answer = call_gemini(prompt)
            result = {"answer": answer, "mode": "general"}

        logger.info(f"Manager returning: {result}")
        return result

    except Exception as e:
        logger.exception("Error in manager_agent")
        return {"error": str(e), "mode": "failed"}

