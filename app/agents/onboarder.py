from __future__ import annotations
import json
from typing import Any
from app.adapters.zetaris import StatementRunner, ZetarisClient, ZetarisError
from app.agents.base import BaseAgent
from app.agents.schemas import OnboardResult, ModelInfo, Plan, Verdict
from app.onboarding.catalog import CATALOG, DEPENDENCIES
from app.onboarding.recipe_loader import load_statements
from app.onboarding import policy
from app.onboarding.registry import LedgerService, ModelRegistry, definition_hash
from app.zsql.classify import statement_target


class OnboarderAgent(BaseAgent):
    name = "OnboarderAgent"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.ledger = LedgerService(ctx.cfg.data_dir)
        self.registry = ModelRegistry(ctx.cfg.data_dir)

    def onboard(self, plan: Plan) -> OnboardResult:
        query_desc = " ".join(s.description or s.url or s.recipe_id or "" for s in plan.sources)
        mem = self.memory_block("onboarding " + query_desc, tags=["onboarding", "zetaris"])

        result = OnboardResult(status="ok")
        statements: list[dict] = []

        # 1. Collect statements for catalog sources
        for src in plan.sources:
            if src.kind == "catalog" and src.recipe_id:
                dep_ids = DEPENDENCIES.get(src.recipe_id, [src.recipe_id])
                for rid in dep_ids:
                    if rid in CATALOG:
                        recipe = CATALOG[rid]
                        stmts = load_statements(self.ctx.cfg, recipe)
                        for s in stmts:
                            s["recipe_id"] = rid
                            s["source_id"] = src.id
                        statements.extend(stmts)
            elif src.kind == "rest_url":
                from app.onboarding.novel_rest import plan_novel_source
                novel_stmts = plan_novel_source(src, self.ctx, mem, result)
                statements.extend(novel_stmts)

        result.plan_statements = [
            {"sql": s["sql"], "kind": s["kind"], "purpose": s.get("purpose", s.get("source_file", "onboard"))}
            for s in statements
        ]

        # 2. Dry-run + Policy gate
        allowed_objects = self._get_allowed_objects(plan)
        run_created = self.ledger.created_in_run(self.ctx.run_id)

        decisions = [
            policy.evaluate(
                s,
                allowed_objects=allowed_objects,
                run_created=run_created,
                approval_mode=self.ctx.cfg.approval_mode,
            )
            for s in statements
        ]

        for s, d in zip(statements, decisions):
            self.ctx.tracer.emit(
                self.name,
                "policy_decision",
                s["sql"],
                d.reason,
                status="success" if d.allowed else "blocked",
            )

        if any(not d.allowed for d in decisions):
            result.status = "failed"
            result.errors = [d.reason for d in decisions if not d.allowed]
            return result

        if any(d.needs_human for d in decisions):
            self.ctx.tracer.emit(
                self.name,
                "approval_requested",
                f"{len(statements)} statements require approval",
                "waiting for POST /approve",
            )
            result.status = "needs_approval"
            return result

        # 3. Execute approved statements
        return self._execute(statements, plan, result)

    def _execute(self, statements: list[dict], plan: Plan, result: OnboardResult) -> OnboardResult:
        client = ZetarisClient(self.ctx.cfg, "builder")
        runner = StatementRunner(client, self.ctx.cfg, self.ctx.tracer, self.name)

        for s in statements:
            target = s.get("target")
            target_name = target[1][0] if target and target[1] else s["sql"][:30]

            if self._exists_and_matches(runner, s):
                result.reused_objects.append(target_name)
                continue

            try:
                runner.execute(
                    s["sql"],
                    purpose=s.get("purpose", "onboard"),
                    rate_limited=s.get("rate_limited", False),
                )
                self.ledger.add(
                    run_id=self.ctx.run_id,
                    kind=s.get("kind", "STATEMENT"),
                    name=target_name,
                    sql=s["sql"],
                    origin=s.get("source_file", "onboarding"),
                )
                result.created_objects.append(target_name)
            except ZetarisError as err:
                if s.get("is_novel_view"):
                    from app.onboarding.repair import attempt_repair
                    fixed = attempt_repair(runner, s, err, self.ctx, plan, result)
                    if not fixed:
                        result.status = "failed"
                        result.errors.append(f"{err.info.kind}: {err.info.message}")
                        return result
                else:
                    result.status = "failed"
                    result.errors.append(f"{err.info.kind}: {err.info.message}")
                    return result

        result.models = self._describe_models(runner, plan)
        return result

    def _exists_and_matches(self, runner: StatementRunner, stmt: dict) -> bool:
        target = stmt.get("target")
        if not target:
            return False
        kind, names = target
        obj_name = names[0].lower()

        # Check existing model registry
        existing = self.registry.get(obj_name)
        if existing and existing.get("definition_hash") == definition_hash(stmt["sql"]):
            return True

        # Fast probe for USL tables: test if count query succeeds
        if kind == "USL_TABLE":
            try:
                res = runner.scalar(f"SELECT COUNT(*) FROM {obj_name}", purpose="check if USL table active")
                if res is not None:
                    return True
            except Exception:
                pass

        return False

    def _describe_models(self, runner: StatementRunner, plan: Plan) -> list[ModelInfo]:
        models: list[ModelInfo] = []
        for src in plan.sources:
            if src.kind == "catalog":
                if src.recipe_id == "ny_taxi_zone_audit":
                    for tbl in ["NY_TAXI.taxi_zones", "NY_TAXI.zones"]:
                        models.append(self._probe_model(runner, tbl, "lightning_table", src.id))
                elif src.recipe_id == "ny_taxi_payments":
                    for tbl in ["NY_TAXI.trips", "NY_TAXI.trip_payments"]:
                        models.append(self._probe_model(runner, tbl, "lightning_table", src.id))
                elif src.recipe_id == "sic_edgar_usl":
                    # Two tables in sic_edgar_usl
                    for tbl in ["lightning.metastore.usl_demo.sic_edgar_usl.company", "lightning.metastore.usl_demo.sic_edgar_usl.sic_code"]:
                        m = self._probe_model(runner, tbl, "usl_table", src.id)
                        models.append(m)
                elif src.recipe_id == "company_dns_v3_raw":
                    m = self._probe_model(runner, "company_dns.sic_codes_raw", "lightning_table", src.id, count_path="data.sics")
                    models.append(m)
            elif src.kind == "rest_url":
                # Probe the created schemastore view
                slug = src.id.lower()
                view_fqn = f"pl_{slug}_{self.ctx.run_id[:6]}_c.pl_{slug}_view"
                m = self._probe_model(runner, view_fqn, "schemastore_view", src.id, count_path=src.count_path)
                models.append(m)
        return models

    def _probe_model(
        self,
        runner: StatementRunner,
        fqn: str,
        kind: str,
        source_id: str,
        count_path: str | None = None,
    ) -> ModelInfo:
        columns: list[dict] = []
        source_row_count = None
        try:
            # UNKNOWN U1: probe structure with LIMIT 1
            res = runner.execute(f"SELECT * FROM {fqn} LIMIT 1", purpose=f"describe {fqn}")
            if res.columns:
                columns = [{"name": c, "type": "STRING"} for c in res.columns]
            cnt = runner.count(fqn, purpose=f"count {fqn}")
            source_row_count = cnt if cnt >= 0 else None
        except Exception:
            pass

        info = ModelInfo(
            fqn=fqn,
            kind=kind,
            columns=columns,
            row_grain=f"one row per {fqn.split('.')[-1]} record",
            source_id=source_id,
            count_path=count_path,
            source_row_count=source_row_count,
        )
        self.registry.put(fqn, info.model_dump())
        return info

    def reconsider(self, plan: Plan, onboard: OnboardResult, verdict: Verdict) -> OnboardResult:
        """Called when Skeptic flags a source-level finding."""
        for finding in verdict.findings:
            if finding.level == "source":
                for model in onboard.models:
                    model.notes.append(f"Source finding: {finding.claim}")
                    self.registry.put(model.fqn, model.model_dump())
        return onboard

    def _get_allowed_objects(self, plan: Plan) -> set[str]:
        allowed = set()
        for src in plan.sources:
            if src.kind == "catalog" and src.recipe_id in CATALOG:
                for recipe_id in DEPENDENCIES.get(src.recipe_id, [src.recipe_id]):
                    allowed.update(CATALOG[recipe_id].allowed_objects)
            elif src.kind == "rest_url":
                allowed.add(f"pl_{src.id.lower()}_{self.ctx.run_id[:6]}")
                allowed.add(f"pl_{src.id.lower()}_raw")
                allowed.add(f"pl_{src.id.lower()}_{self.ctx.run_id[:6]}_c")
                allowed.add(f"pl_{src.id.lower()}_view")
        return {a.lower() for a in allowed}
