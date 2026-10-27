from __future__ import annotations
from unittest.mock import MagicMock
from app.agents.orchestrator import run_pipeline
from app.agents.schemas import Plan, OnboardResult, Draft, Verdict, Finding, FinalAnswer
from app.verify.evidence import EvidencePack, CheckResult


def test_orchestrator_feedback_loop_revision_and_approval():
    ctx = MagicMock()
    ctx.cfg.max_revisions = 2
    ctx.run_id = "test_run_123"
    ctx.memory = None

    planner = MagicMock()
    planner.plan.return_value = Plan(
        objective="Analyze companies",
        question="Which companies disagree?",
        sources=[],
    )
    planner.finalise.return_value = FinalAnswer(
        summary="Final verified summary",
        findings=[],
        caveats=[],
        confidence=0.9,
    )

    onboarder = MagicMock()
    onboarder.onboard.return_value = OnboardResult(status="ok", models=[])

    investigator = MagicMock()
    draft1 = Draft(sql="SELECT * FROM c INNER JOIN s ON c.k = s.k", summary="Draft 1 summary", revision=0)
    draft2 = Draft(sql="SELECT * FROM c LEFT JOIN s ON c.k = s.k", summary="Draft 2 revised summary", revision=1)
    investigator.answer.return_value = draft1
    investigator.revise.return_value = draft2

    # Verifier evidence
    pack_fail = EvidencePack()
    pack_fail.add(CheckResult(id="join_loss", name="join_loss", status="fail", blocking=True, detail="dropped 1 key"))

    pack_pass = EvidencePack()
    pack_pass.add(CheckResult(id="join_loss", name="join_loss", status="pass", blocking=True, detail="all matched"))

    verifier = MagicMock()
    verifier.collect.side_effect = [pack_fail, pack_pass]

    # Skeptic verdicts
    verdict1 = Verdict(
        verdict="revise",
        confidence=0.8,
        findings=[Finding(id="f1", severity="blocking", claim="dropped key", evidence_ref="join_loss")],
    )
    verdict2 = Verdict(
        verdict="approve",
        confidence=0.95,
        findings=[],
    )
    skeptic = MagicMock()
    skeptic.review.side_effect = [verdict1, verdict2]

    req = MagicMock()
    req.user_query = "Which companies disagree?"

    res = run_pipeline(req, ctx, planner, onboarder, investigator, skeptic, verifier)

    assert res["status"] == "verified"
    assert res["revisions"] == 1
    assert investigator.revise.call_count == 1
    assert skeptic.review.call_count == 2
    assert planner.finalise.call_count == 1
