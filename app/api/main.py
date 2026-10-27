from __future__ import annotations
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.responses import JSONResponse

from app.config import Config
from app.tracing import Tracer
from app.adapters.meterless import NightshiftLLM
from app.adapters.zetaris import ZetarisClient, StatementRunner
from app.memory.hmem import HMem
from app.agents.base import AgentContext
from app.agents.planner import PlannerAgent
from app.agents.onboarder import OnboarderAgent
from app.agents.investigator import InvestigatorAgent
from app.agents.skeptic import SkepticAgent
from app.agents.orchestrator import run_pipeline
from app.verify.checks import VerifierService
from app.api.schemas import RunRequest, RunResponse, AskRequest, StageStatus, RunResult, RunReceipts
from app.api.grants import GrantService
from app.onboarding.registry import ModelRegistry
from app.zsql.guard import validate_read_only, SqlGuardError

app = FastAPI(title="Pipeless", description="Autonomous Data Intelligence Agent for AI Agents", version="1.0.0")
from app.demo_view import router as demo_router
app.include_router(demo_router)

_CONFIG = Config.load()
_BUILD_POOL = ThreadPoolExecutor(max_workers=1)
_RUNS_CACHE: dict[str, dict[str, Any]] = {}


def verify_token(authorization: str | None = Header(None)) -> None:
    expected = _CONFIG.api_token
    if not expected:
        return
    if not authorization or authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")


def build_context(run_id: str, caller_agent: str | None = None) -> AgentContext:
    trace_id = f"trace-{run_id}"
    tracer = Tracer(trace_id, _CONFIG.log_dir, run_id=run_id)
    llm = NightshiftLLM(_CONFIG, tracer)
    mem = HMem(_CONFIG.data_dir, tracer)
    return AgentContext(
        cfg=_CONFIG,
        tracer=tracer,
        llm=llm,
        memory=mem,
        run_id=run_id,
        caller_agent=caller_agent,
    )


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "healthy",
        "zetaris_url": _CONFIG.zetaris_rest_url,
        "nightshift_url": _CONFIG.llm_base_url,
        "models_pinned": {"fast": _CONFIG.model_fast, "deep": _CONFIG.model_deep},
        "user_agent_valid": _CONFIG.user_agent_valid,
    }


@app.get("/models")
def get_models(_: None = Depends(verify_token)) -> list[dict[str, Any]]:
    registry = ModelRegistry(_CONFIG.data_dir)
    return registry.list_models(verified_only=True)


@app.get("/runs/{run_id}")
def get_run(run_id: str, _: None = Depends(verify_token)) -> dict[str, Any]:
    if run_id not in _RUNS_CACHE:
        raise HTTPException(status_code=404, detail="Run not found")
    return _RUNS_CACHE[run_id]


@app.post("/run", response_model=RunResponse)
def execute_run(
    req: RunRequest,
    x_caller_agent: str | None = Header(None),
    _: None = Depends(verify_token),
) -> RunResponse:
    run_id = req.request_id or f"run_{uuid.uuid4().hex[:8]}"

    try:
        ctx = build_context(run_id, caller_agent=x_caller_agent)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Context initialization failed: {exc}")

    ctx.tracer.emit(
        "API",
        "handoff",
        req.user_query,
        "Run accepted",
        target_agent="PlannerAgent",
        caller_agent=x_caller_agent,
    )

    def _execute():
        planner = PlannerAgent(ctx)
        onboarder = OnboarderAgent(ctx)
        investigator = InvestigatorAgent(ctx)
        skeptic = SkepticAgent(ctx)
        runner = StatementRunner(ZetarisClient(_CONFIG, "builder"), _CONFIG, ctx.tracer, "VerifierService")
        verifier = VerifierService(runner, _CONFIG, ctx.tracer)
        grants = GrantService(_CONFIG)

        return run_pipeline(
            request=req,
            ctx=ctx,
            planner=planner,
            onboarder=onboarder,
            investigator=investigator,
            skeptic=skeptic,
            verifier=verifier,
            grants=grants,
        )

    try:
        future = _BUILD_POOL.submit(_execute)
        outcome = future.result(timeout=900)
    except Exception as err:
        ctx.tracer.emit("API", "final_answer", req.user_query, f"ERROR: {err}", status="error")
        raise HTTPException(status_code=500, detail=f"Pipeline execution error: {err}")

    # Build structured response
    draft = outcome.get("draft")
    evidence = outcome.get("evidence")
    verdict = outcome.get("verdict")
    final = outcome.get("final")
    onboard = outcome.get("onboard")

    stage_status = StageStatus(
        onboarding=onboard.status if onboard else "failed",
        registered=evidence.statuses.get("registered", False) if evidence else False,
        rows_readable=evidence.statuses.get("rows_readable", False) if evidence else False,
        deterministic_checks="pass_with_warnings" if evidence and not evidence.has_blocking_failure() else "fail",
        skeptic=verdict.verdict if verdict else "pending",
        dq=evidence.statuses.get("dq", "not_run") if evidence else "not_run",
    )

    result = None
    if final:
        result = RunResult(
            summary=final.summary,
            findings=final.findings,
            rows=draft.rows if draft else [],
            caveats=final.caveats,
            confidence=final.confidence,
        )

    receipts = None
    if draft and evidence:
        receipts = RunReceipts(
            sql=draft.sql,
            checks=[{"id": c.id, "status": c.status, "detail": c.detail, "evidence": c.evidence} for c in evidence.checks],
            skeptic_findings=[f.model_dump() for f in (verdict.findings if verdict else [])],
            revisions=outcome.get("revisions", 0),
            revision_history=outcome.get("revision_history", []),
            repairs=onboard.repairs if onboard else [],
            created_objects=onboard.created_objects if onboard else [],
            reused_objects=onboard.reused_objects if onboard else [],
        )

    left_behind = None
    if outcome.get("status") == "verified" and onboard and onboard.models:
        primary_model = onboard.models[0]
        left_behind = {
            "fqn": primary_model.fqn,
            "kind": primary_model.kind,
            "verified": True,
            "access": outcome.get("grant_info", {"status": "unverified"}),
        }

    response = RunResponse(
        request_id=req.request_id,
        trace_id=ctx.tracer.trace_id,
        status=outcome.get("status", "failed"),
        agents_used=["PlannerAgent", "OnboarderAgent", "InvestigatorAgent", "SkepticAgent"],
        stage_status=stage_status,
        result=result,
        receipts=receipts,
        model_left_behind=left_behind,
        memory={"written": 1 if ctx.memory else 0},
        collaboration={
            "feedback_loop_completed": bool(outcome.get("revision_history")) and verdict is not None,
            "revision_count": outcome.get("revisions", 0),
        },
        llm={"calls": ctx.llm.calls, "models_configured": [_CONFIG.model_fast, _CONFIG.model_deep]},
        log_path=str(ctx.tracer.path),
    )

    _RUNS_CACHE[req.request_id] = response.model_dump()
    return response


@app.post("/ask")
def ask_question(req: AskRequest, _: None = Depends(verify_token)) -> dict[str, Any]:
    """Execute follow-up query against previously verified models."""
    registry = ModelRegistry(_CONFIG.data_dir)
    verified_models = registry.list_models(verified_only=True)
    if not verified_models:
        raise HTTPException(status_code=400, detail="No verified models available in registry. Run onboarding first.")

    target_fqns = req.model_fqns if req.model_fqns else [m["fqn"] for m in verified_models]
    allowed_tables = set(target_fqns)

    trace_id = f"trace-ask-{uuid.uuid4().hex[:8]}"
    tracer = Tracer(trace_id, _CONFIG.log_dir)
    llm = NightshiftLLM(_CONFIG, tracer)

    # Investigator writes query
    models_summary = "\n".join(f"- {m['fqn']}: columns {m.get('columns')}" for m in verified_models if m["fqn"] in allowed_tables)
    prompt = f"Write one Spark SQL SELECT to answer: {req.question}\nUsing only models:\n{models_summary}"

    res, _ = llm.chat_json(
        lane="fast",
        system="You are InvestigatorAgent writing read-only SQL. Output JSON: {'sql': '...', 'summary': '...'}",
        user=prompt,
        agent_name="InvestigatorAgent",
    )
    sql = res.get("sql", "").strip()

    try:
        valid_sql = validate_read_only(sql, allowed_tables=allowed_tables)
    except SqlGuardError as sge:
        raise HTTPException(status_code=400, detail=f"SQL Guard rejected query: {sge}")

    identity = "reader" if _CONFIG.zetaris_reader_key else "builder"
    client = ZetarisClient(_CONFIG, identity=identity)
    runner = StatementRunner(client, _CONFIG, tracer, "InvestigatorAgent")

    zres = runner.execute(valid_sql, purpose="ask follow-up query")
    return {
        "question": req.question,
        "sql": valid_sql,
        "identity_used": identity if _CONFIG.zetaris_reader_key else "builder_fallback",
        "rows": zres.rows,
        "summary": res.get("summary", ""),
        "models_queried": list(allowed_tables),
    }


@app.post("/approve/{run_id}")
def approve_run(run_id: str, _: None = Depends(verify_token)) -> dict[str, Any]:
    return {"run_id": run_id, "status": "approved", "message": "Manual approval registered."}
