"""Review or apply the bounded SIC/EDGAR audit recipe; no secrets in output."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import requests

from app.config import Config
from app.tracing import Tracer
from app.agents.onboarder import OnboarderAgent
from app.agents.schemas import Plan, SourceSpec
from app.onboarding.catalog import CATALOG, DEPENDENCIES
from app.onboarding.recipe_loader import load_statements
from app.onboarding.policy import evaluate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    cfg = Config.load()
    plan = Plan(objective="Audit company industry mappings", question="Account for every company and identify missing SIC mappings",
                sources=[SourceSpec(id="sic_edgar", kind="catalog", recipe_id="sic_edgar_usl")], needs_join=True)
    tracer = Tracer("prepare_live_audit", cfg.log_dir)
    ctx = SimpleNamespace(cfg=cfg, tracer=tracer, memory=None, run_id="prepare_live_audit")
    onboarder = OnboarderAgent(ctx)
    allowed = onboarder._get_allowed_objects(plan)
    statements = []
    for recipe_id in DEPENDENCIES["sic_edgar_usl"]:
        for statement in load_statements(cfg, CATALOG[recipe_id]):
            decision = evaluate(statement, allowed_objects=allowed, run_created=set(), approval_mode=cfg.approval_mode)
            statements.append(statement)
            print(json.dumps({"recipe": recipe_id, "kind": statement["kind"], "target": statement["target"],
                              "allowed": decision.allowed, "reason": decision.reason}), flush=True)
            if not decision.allowed or decision.needs_human:
                raise RuntimeError("Recipe plan is not approved by configured policy")
    out = cfg.data_dir / "demo"
    out.mkdir(parents=True, exist_ok=True)
    (out / "onboarding-plan.sql").write_text("\n\n".join(
        s["sql"].replace(cfg.user_agent, "PIPELESS CONTACT_REDACTED") + ";" for s in statements), encoding="utf-8")
    if not args.apply:
        print(f"PLAN ONLY: {len(statements)} statements; nothing sent to Zetaris.", flush=True)
        return
    source_checks = {}
    for name, url in [("sic", CATALOG["company_dns_v3_raw"].source_count["url"]),
                      ("ibm", "https://data.sec.gov/submissions/CIK0000051143.json")]:
        response = requests.get(url, headers={"User-Agent": cfg.user_agent}, timeout=25)
        source_checks[name] = {"http_status": response.status_code}
        print(json.dumps({"source_preflight": name, **source_checks[name]}), flush=True)
        response.raise_for_status()
        body = response.json()
        if name == "sic":
            source_checks[name]["independent_sic_count"] = len(body["data"]["sics"])
        else:
            source_checks[name]["independent_ibm_sic"] = str(body["sic"])
    result = onboarder.onboard(plan)
    evidence = {"source_checks": source_checks, "onboarding": result.model_dump()}
    # Plans contain the contact header; redact it in shareable evidence.
    text = json.dumps(evidence, indent=2).replace(cfg.user_agent, "PIPELESS CONTACT_REDACTED")
    (out / "onboarding-result.json").write_text(text, encoding="utf-8")
    print(json.dumps({"status": result.status, "models": [m.fqn for m in result.models],
                      "created_count": len(result.created_objects), "errors": result.errors}), flush=True)
    if result.status != "ok" or not result.models or any(not m.columns or m.source_row_count is None for m in result.models):
        raise RuntimeError("Onboarding or model readability was not verified; inspect saved evidence")


if __name__ == "__main__":
    main()
