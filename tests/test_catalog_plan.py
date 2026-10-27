from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.agents.onboarder import OnboarderAgent
from app.agents.schemas import Plan, SourceSpec
from app.onboarding.catalog import CATALOG
from app.onboarding.policy import evaluate
from app.onboarding.recipe_loader import load_statements
from app.zsql.classify import statement_target


def test_usl_allowlist_includes_required_source_objects(tmp_path):
    ctx = SimpleNamespace(cfg=SimpleNamespace(data_dir=tmp_path))
    agent = OnboarderAgent(ctx)
    plan = Plan(objective="audit", question="audit", sources=[SourceSpec(id="x", kind="catalog", recipe_id="sic_edgar_usl")])
    allowed = agent._get_allowed_objects(plan)
    for sql in ["CREATE LIGHTNING DATABASE SEC_DATA", "CREATE LIGHTNING REST TABLE apple_submissions_raw FROM SEC_DATA REQUEST()",
                "UPDATE USL lightning.metastore.usl_demo.sic_edgar_usl.company SET TABLE DESCRIPTION 'audit'"]:
        assert evaluate({"kind": "CREATE", "target": statement_target(sql)}, allowed_objects=allowed,
                        run_created=set(), approval_mode="auto_policy").allowed
    assert "unrelated_source" not in allowed


def test_sec_database_recipe_uses_only_namespace_statement(tmp_path):
    relative = "open_data/rest_apis/sql/rate_limited/01_edgar_company_facts_create.sql"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text("CREATE LIGHTNING DATABASE SEC_DATA; CREATE LIGHTNING REST TABLE unrelated FROM SEC_DATA REQUEST();")
    statements = load_statements(SimpleNamespace(repo_path=tmp_path), CATALOG["sec_data_database"])
    assert len(statements) == 1
    assert statements[0]["target"] == ("LIGHTNING_DATABASE", ("SEC_DATA",))
