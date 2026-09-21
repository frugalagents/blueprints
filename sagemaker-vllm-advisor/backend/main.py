from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from httpx import HTTPError

from backend.agent.bedrock_agent import BedrockAdvisorAgent
from backend.domain.models import (
    AdvisorResult,
    AgentAnswer,
    AgentQuestion,
    AgentTraceStep,
    AssessmentInput,
    DeploymentPlan,
    DeploymentPlanRequest,
    ModelMetadata,
    ModelSearchResult,
)
from backend.providers.huggingface import HuggingFaceProvider
from backend.services.advisor_service import analyze_assessment
from backend.services.deployment_plan import create_deployment_plan

app = FastAPI(
    title="SageMaker vLLM Model Deployment Advisor",
    version="0.2.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:4173",
        "http://localhost:4173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "awsWritesEnabled": False,
        "bedrockModelId": os.getenv(
            "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6"
        ),
    }


@app.get("/api/models/search", response_model=list[ModelSearchResult])
async def search_models(q: str = Query(min_length=2, max_length=200)):
    try:
        return await HuggingFaceProvider(os.getenv("HF_TOKEN")).search(q)
    except HTTPError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.get("/api/models/resolve", response_model=ModelMetadata)
async def resolve_model(model_id: str = Query(min_length=3, max_length=300)):
    try:
        return await HuggingFaceProvider(os.getenv("HF_TOKEN")).resolve(model_id)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except HTTPError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.post("/api/assessments/analyze", response_model=AdvisorResult)
def analyze_endpoint(
    assessment: AssessmentInput,
    live_aws: bool = Query(default=True),
):
    try:
        return analyze_assessment(assessment, include_live_aws=live_aws)
    except Exception as error:
        if live_aws:
            fallback = analyze_assessment(assessment, include_live_aws=False)
            fallback.trace.append(
                AgentTraceStep(
                    phase="Observe",
                    title="Live AWS evidence unavailable",
                    detail=str(error),
                    evidence="aws",
                )
            )
            return fallback
        raise


@app.post("/api/agent/ask", response_model=AgentAnswer)
async def ask_agent(question: AgentQuestion):
    try:
        return await BedrockAdvisorAgent().ask(question)
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.post("/api/deployment-plans", response_model=DeploymentPlan)
def deployment_plan(request: DeploymentPlanRequest):
    return create_deployment_plan(request)
