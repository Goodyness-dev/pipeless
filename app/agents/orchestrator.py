from __future__ import annotations
from typing import Any
from app.agents.schemas import Plan, Verdict, Draft, FinalAnswer, OnboardResult
from app.verify.evidence import EvidencePack


def run_pipeline(
    request: Any,
    ctx: Any,
    planner: Any,
    onboarder: Any,
    investigator: Any,
    skeptic: Any,
    verifier: Any,
    grants: Any = None,
) -> dict[str, Any]:
    t = ctx.tracer
    plan = planner.plan(request)
    t.emit("PlannerAgent", "plan_created", request.user_query, plan.objective)

    planner.handoff("OnboarderAgent", "onboard sources")
    onboard = onboarder.onboard(plan)
    if onboard.status == "needs_approval":
        return {"status": "needs_approval", "plan": plan, "onboard": onboard}
    if onboard.status == "failed":
        return {"status": "failed", "stage": "onboarding", "plan": plan, "onboard": onboard}

    onboarder.handoff("InvestigatorAgent", "models ready")
    draft = investigator.answer(plan, onboard)

    investigator.handoff("VerifierService", "evidence request")
    evidence = verifier.collect(plan, onboard, draft)

    def snapshot(current_draft, current_evidence):
        return {"revision": current_draft.revision, "sql": current_draft.sql,
                "returned_rows": len(current_draft.rows), "checks": current_evidence.to_prompt_json()}

    revision_history = [snapshot(draft, evidence)]

    verdict: Verdict | None = None
    revisions = 0

    for _round in range(ctx.cfg.max_revisions + 1):
        verdict = skeptic.review(plan, draft, evidence)

        # Hard rule: a deterministic blocking failure can never be approved
        if verdict.verdict == "approve" and evidence.has_blocking_failure():
            verdict.verdict = "revise"
            verdict.findings.append(evidence.as_blocking_finding())

        t.emit(
            "SkepticAgent",
            "skeptic_verdict",
            plan.question,
            verdict.verdict,
            target_agent="InvestigatorAgent",
            confidence=verdict.confidence,
            findings=[f.model_dump() for f in verdict.findings],
        )

        if verdict.verdict in ("approve", "reject") or _round == ctx.cfg.max_revisions:
            break

        revisions += 1
        t.emit(
            "SkepticAgent",
            "revision_requested",
            plan.question,
            f"{len(verdict.findings)} findings",
            target_agent="InvestigatorAgent",
            retry_count=revisions,
        )

        if any(f.level == "source" for f in verdict.findings):
            onboard = onboarder.reconsider(plan, onboard, verdict)

        draft = investigator.revise(plan, draft, verdict, evidence, onboard)
        t.emit(
            "InvestigatorAgent",
            "revision_applied",
            draft.sql,
            draft.summary,
            target_agent="SkepticAgent",
            retry_count=revisions,
        )
        evidence = verifier.collect(plan, onboard, draft)
        revision_history.append(snapshot(draft, evidence))

    planner.handoff("PlannerAgent", "finalise answer")
    final = planner.finalise(plan, draft, evidence, verdict)
    final_status = _determine_status(verdict, evidence)

    # If verified, flip registry status and grant read access
    grant_info = None
    if final_status == "verified" and onboard.models:
        for model in onboard.models:
            onboarder.registry.set_verified(model.fqn, True, caveats=final.caveats)
            if grants:
                grant_info = grants.grant_read(onboarder, model)

    # Write H-MEM outcome event
    if ctx.memory:
        ctx.memory.add(
            content=f"Run outcome {final_status} after {revisions} revisions for query: {plan.question[:80]}",
            type="factual",
            layer="short_term",
            tags=["outcome", final_status],
            agent_id="Orchestrator",
            run_id=ctx.run_id,
            confidence=0.8 if final_status == "verified" else 0.5,
        )
        if final_status == "verified":
            join_check = next((c for c in evidence.checks if c.id == "join_loss"), None)
            if revisions and draft.join_spec and join_check and join_check.status in ("pass", "warn"):
                js = draft.join_spec
                ctx.memory.add(
                    content=(f"Verified join repair for {js.left} and {js.right}: keep the left {js.left_key} "
                             f"in output and measure actual key retention through result filters. "
                             f"Observed audit: {join_check.detail}. Provenance: run {ctx.run_id}, "
                             "join_loss check and saved revision history. Recheck counts on future runs."),
                    type="procedural", layer="short_term", tags=["verification", "join", "model"],
                    entities=[js.left, js.right], agent_id="Orchestrator", run_id=ctx.run_id,
                    confidence=0.85, namespace="audit/join-repair",
                )
            ctx.memory.promote_run(ctx.run_id)

    return {
        "status": final_status,
        "plan": plan,
        "onboard": onboard,
        "draft": draft,
        "evidence": evidence,
        "verdict": verdict,
        "final": final,
        "revisions": revisions,
        "revision_history": revision_history,
        "grant_info": grant_info,
    }


def _determine_status(verdict: Verdict | None, evidence: EvidencePack) -> str:
    if not verdict:
        return "failed"
    if verdict.verdict == "approve" and not evidence.has_blocking_failure():
        return "verified"
    if verdict.verdict == "reject":
        return "failed"
    return "answered_with_caveats"
