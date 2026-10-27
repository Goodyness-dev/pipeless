from __future__ import annotations
from app.onboarding.policy import evaluate


def test_policy_read_allowed():
    stmt = {"kind": "READ", "sql": "SELECT 1"}
    decision = evaluate(stmt, allowed_objects={"any"}, run_created=set(), approval_mode="auto_policy")
    assert decision.allowed is True
    assert decision.needs_human is False


def test_policy_destructive_blocked_if_not_in_run():
    stmt = {
        "kind": "DESTRUCTIVE",
        "sql": "DROP LIGHTNING DATABASE db_other",
        "target": ("LIGHTNING_DATABASE", ("db_other",)),
    }
    decision = evaluate(stmt, allowed_objects={"any"}, run_created={"my_db"}, approval_mode="auto_policy")
    assert decision.allowed is False
    assert decision.needs_human is True


def test_policy_destructive_allowed_if_created_by_run():
    stmt = {
        "kind": "DESTRUCTIVE",
        "sql": "DROP LIGHTNING DATABASE my_db",
        "target": ("LIGHTNING_DATABASE", ("my_db",)),
    }
    decision = evaluate(stmt, allowed_objects={"any"}, run_created={"my_db"}, approval_mode="auto_policy")
    assert decision.allowed is True
    assert decision.needs_human is False


def test_policy_allowlisted_create():
    stmt = {
        "kind": "CREATE",
        "sql": "CREATE LIGHTNING DATABASE company_dns",
        "target": ("LIGHTNING_DATABASE", ("company_dns",)),
    }
    decision = evaluate(stmt, allowed_objects={"company_dns"}, run_created=set(), approval_mode="auto_policy")
    assert decision.allowed is True
    assert decision.needs_human is False


def test_policy_non_allowlisted_create():
    stmt = {
        "kind": "CREATE",
        "sql": "CREATE LIGHTNING DATABASE evil_db",
        "target": ("LIGHTNING_DATABASE", ("evil_db",)),
    }
    decision = evaluate(stmt, allowed_objects={"company_dns"}, run_created=set(), approval_mode="auto_policy")
    assert decision.allowed is False
    assert decision.needs_human is True


def test_policy_manual_mode_requires_human():
    stmt = {
        "kind": "CREATE",
        "sql": "CREATE LIGHTNING DATABASE company_dns",
        "target": ("LIGHTNING_DATABASE", ("company_dns",)),
    }
    decision = evaluate(stmt, allowed_objects={"company_dns"}, run_created=set(), approval_mode="manual")
    assert decision.allowed is True
    assert decision.needs_human is True
