from __future__ import annotations
from app.zsql.playbook import classify_error


def test_classify_rate_limited():
    err = classify_error(429, "Too Many Requests: OVER_RATE_LIMIT")
    assert err.kind == "rate_limited"
    assert err.retryable is True
    assert err.wait_seconds == 60


def test_classify_bad_gateway():
    err = classify_error(502, "Bad Gateway from upstream")
    assert err.kind == "bad_gateway"
    assert err.retryable is True


def test_classify_unresolved_column():
    err = classify_error(200, "cannot resolve '`entityName`' given input columns")
    assert err.kind == "unresolved_column"
    assert err.retryable is False
    assert "Backtick" in err.hint


def test_classify_already_exists():
    err = classify_error(400, "Container already exists")
    assert err.kind == "already_exists"
    assert "Inspect its definition" in err.hint


def test_classify_repeated_exploded_view():
    err = classify_error(500, "MISSING_ATTRIBUTES.RESOLVED_ATTRIBUTE_APPEAR_IN_OPERATION")
    assert err.kind == "repeated_exploded_view"
    assert "window function" in err.hint
