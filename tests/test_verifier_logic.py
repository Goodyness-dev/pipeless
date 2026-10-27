from __future__ import annotations
from unittest.mock import MagicMock
import sqlite3
from types import SimpleNamespace
import pytest
from app.verify.checks import count_at_path, VerifierService
from app.verify.evidence import EvidencePack, CheckResult
from app.agents.schemas import Draft, JoinSpec, ModelInfo, OnboardResult


def test_count_at_path():
    data = {
        "data": {
            "sics": [{"code": "100"}, {"code": "200"}, {"code": "300"}],
            "dict_keys": {"a": 1, "b": 2},
        }
    }
    assert count_at_path(data, "data.sics") == 3
    assert count_at_path(data, "data.dict_keys") == 2
    assert count_at_path(data, "data.nonexistent") == 0


@pytest.mark.parametrize("status", ["fail", "unverified", "skipped"])
def test_unresolved_required_check_blocks_verification(status):
    pack = EvidencePack(checks=[CheckResult('required', 'Required evidence', status, True, 'not established')])
    assert pack.has_blocking_failure()
    assert pack.as_blocking_finding().evidence_ref == 'required'


def test_join_loss_fails_when_inner_join_drops_rows():
    mock_runner = MagicMock()
    # left_total = 7, matched = 6
    mock_runner.scalar.side_effect = ["7", "6", "0"]
    unm_res = MagicMock()
    unm_res.rows = [["3570"]]
    mock_runner.execute.return_value = unm_res

    service = VerifierService(mock_runner, None, MagicMock())

    draft = Draft(
        sql="SELECT c.name, s.desc FROM company c INNER JOIN sic s ON c.sic = s.code",
        join_spec=JoinSpec(left="company", right="sic", left_key="sic", right_key="code"),
        rows=[["IBM", "desc"]],
        columns=["sic", "desc"],
    )

    result = service.check_join_loss(draft)
    assert result.status == "fail"
    assert result.blocking is True
    assert "silently dropped" in result.detail
    assert "3570" in result.evidence["unmatched_keys"]


def test_join_loss_warns_when_left_join_surfaces_unmatched():
    mock_runner = MagicMock()
    mock_runner.scalar.side_effect = ["7", "6", "1"]
    unm_res = MagicMock()
    unm_res.rows = [["3570"]]
    mock_runner.execute.return_value = unm_res

    service = VerifierService(mock_runner, None, MagicMock())

    draft = Draft(
        sql="SELECT c.name, s.desc FROM company c LEFT JOIN sic s ON c.sic = s.code",
        join_spec=JoinSpec(left="company", right="sic", left_key="sic", right_key="code"),
        rows=[["IBM", None]],
        columns=["sic", "desc"],
    )

    result = service.check_join_loss(draft)
    assert result.status == "warn"
    assert result.blocking is True
    assert "1/1 unmatched distinct keys retained" in result.detail
    assert "3570" in result.evidence["unmatched_keys"]


def test_join_loss_passes_when_counts_equal():
    mock_runner = MagicMock()
    mock_runner.scalar.side_effect = ["7", "7", "7"]

    service = VerifierService(mock_runner, None, MagicMock())

    draft = Draft(
        sql="SELECT c.name, s.desc FROM company c INNER JOIN sic s ON c.sic = s.code",
        join_spec=JoinSpec(left="company", right="sic", left_key="sic", right_key="code"),
        columns=["sic"],
    )

    result = service.check_join_loss(draft)
    assert result.status == "pass"


class SQLiteRunner:
    """Execute audit SQL against real local tables, with no network or model."""
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("""
            CREATE TABLE company (name TEXT, sic TEXT);
            CREATE TABLE sic (code TEXT, description TEXT);
            INSERT INTO company VALUES ('Matched', '100'), ('IBM', '3570'), ('Other', '999');
            INSERT INTO sic VALUES ('100', 'Industry');
        """)

    def scalar(self, sql, **kwargs):
        return self.db.execute(sql).fetchone()[0]

    def execute(self, sql, **kwargs):
        cursor = self.db.execute(sql)
        return SimpleNamespace(rows=[list(r) for r in cursor.fetchall()], columns=[c[0] for c in cursor.description])

    def count(self, table, **kwargs):
        return self.scalar(f"SELECT COUNT(*) FROM {table}")


@pytest.fixture
def sql_runner():
    runner = SQLiteRunner()
    yield runner
    runner.db.close()


def audited_draft(runner, sql):
    result = runner.execute(sql)
    return Draft(sql=sql, columns=result.columns, rows=result.rows,
                 join_spec=JoinSpec(left="company", right="sic", left_key="sic", right_key="code"))


@pytest.mark.parametrize("sql,retained", [
    ("SELECT c.sic, s.description FROM company c LEFT JOIN sic s ON c.sic=s.code WHERE s.description IS NOT NULL", 0),
    ("SELECT c.sic, s.description FROM company c LEFT OUTER JOIN sic s ON c.sic=s.code WHERE c.name != 'IBM'", 1),
    ("SELECT c.sic, s.description FROM company c LEFT JOIN sic s ON c.sic=s.code ORDER BY c.sic LIMIT 1", 0),
    ("SELECT c.sic, s.description FROM company c INNER JOIN sic s ON c.sic=s.code /* LEFT JOIN */", 0),
])
def test_real_query_cannot_hide_loss_behind_left_join(sql_runner, sql, retained):
    result = VerifierService(sql_runner, None, MagicMock()).check_join_loss(audited_draft(sql_runner, sql))
    assert result.status == "fail"
    assert result.evidence["unmatched_total"] == 2
    assert result.evidence["unmatched_retained"] == retained


def test_real_left_join_repair_retains_every_unmatched_key(sql_runner):
    draft = audited_draft(sql_runner, "SELECT c.sic, s.description FROM company c LEFT JOIN sic s ON c.sic=s.code")
    result = VerifierService(sql_runner, None, MagicMock()).check_join_loss(draft)
    assert result.status == "warn"  # unresolved source mappings still deserve a caveat
    assert result.evidence["unmatched_retained"] == result.evidence["unmatched_total"] == 2


def test_missing_projected_key_does_not_claim_preservation(sql_runner):
    draft = audited_draft(sql_runner, "SELECT c.name, s.description FROM company c LEFT JOIN sic s ON c.sic=s.code")
    result = VerifierService(sql_runner, None, MagicMock()).check_join_loss(draft)
    assert result.status == "fail"
    assert "Preservation not verified" in result.detail


def test_matching_source_keys_can_still_be_filtered_out(sql_runner):
    sql_runner.db.execute("INSERT INTO sic VALUES ('3570','Industry'), ('999','Industry')")
    draft = audited_draft(sql_runner, "SELECT c.sic FROM company c LEFT JOIN sic s ON c.sic=s.code WHERE c.name != 'IBM'")
    result = VerifierService(sql_runner, None, MagicMock()).check_join_loss(draft)
    assert result.status == "fail"
    assert result.evidence["matched"] == 3
    assert result.evidence["source_keys_retained"] == 2


@pytest.mark.parametrize("counts", [(None, None), ("-1", "-1"), ("7.5", "6"), ("NaN", "6"), ("6", "7")])
def test_invalid_count_evidence_never_passes(counts):
    runner = MagicMock()
    runner.scalar.side_effect = counts
    draft = Draft(sql="SELECT 1", join_spec=JoinSpec(left="company", right="sic", left_key="sic", right_key="code"))
    assert VerifierService(runner, None, MagicMock()).check_join_loss(draft).status == "fail"


@pytest.mark.parametrize("column", ["event_date", "created_at"])
def test_date_column_discovery_does_not_verify_coverage(column):
    runner = MagicMock()
    onboard = OnboardResult(status="ok", models=[ModelInfo(
        fqn="events", kind="lightning_table", columns=[{"name": column, "type": "date"}])])
    result = VerifierService(runner, None, MagicMock()).check_coverage_period(Draft(sql="SELECT * FROM events"), onboard)
    assert result.status == "unverified"
    assert result.evidence["temporal_columns"] == [{"model": "events", "column": column}]
    runner.execute.assert_not_called()


def test_no_temporal_columns_skips_coverage():
    result = VerifierService(MagicMock(), None, MagicMock()).check_coverage_period(
        Draft(sql="SELECT 1"), OnboardResult(status="ok", models=[]))
    assert result.status == "skipped"


@pytest.mark.parametrize("repair_works", [True, False])
def test_real_audit_drives_repair_loop_despite_approving_skeptic(sql_runner, repair_works):
    from app.agents.orchestrator import run_pipeline
    from app.agents.schemas import Plan, Verdict, FinalAnswer

    ctx = MagicMock()
    ctx.cfg.max_revisions = 1
    ctx.memory = None
    request = SimpleNamespace(user_query="Account for every industry code")
    planner = MagicMock()
    planner.plan.return_value = Plan(objective=request.user_query, question=request.user_query, sources=[])
    planner.finalise.return_value = FinalAnswer(summary="Audit output")
    onboarder = MagicMock()
    onboarder.onboard.return_value = OnboardResult(status="ok", models=[
        ModelInfo(fqn="company", kind="lightning_table"), ModelInfo(fqn="sic", kind="lightning_table")])
    bad_sql = "SELECT c.sic, s.description FROM company c LEFT JOIN sic s ON c.sic=s.code WHERE s.code IS NOT NULL"
    good_sql = "SELECT c.sic, s.description FROM company c LEFT JOIN sic s ON c.sic=s.code"
    investigator = MagicMock()
    investigator.answer.return_value = audited_draft(sql_runner, bad_sql)
    investigator.revise.return_value = audited_draft(sql_runner, good_sql if repair_works else bad_sql)
    skeptic = MagicMock()
    # Simulate an overconfident model: the real deterministic gate must override it.
    skeptic.review.side_effect = lambda *args: Verdict(verdict="approve")
    verifier = VerifierService(sql_runner, None, ctx.tracer)

    result = run_pipeline(request, ctx, planner, onboarder, investigator, skeptic, verifier)

    assert result["revisions"] == 1
    assert investigator.revise.call_count == 1
    finding = investigator.revise.call_args.args[2].findings[0]
    assert finding.evidence_ref == "join_loss"
    assert result["status"] == ("verified" if repair_works else "answered_with_caveats")
    check = next(c for c in result["evidence"].checks if c.id == "join_loss")
    assert check.evidence["unmatched_retained"] == (2 if repair_works else 0)
