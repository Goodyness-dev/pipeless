from __future__ import annotations
import pytest
from app.zsql.guard import validate_read_only, SqlGuardError


def test_guard_allows_safe_select():
    sql = "SELECT id, name FROM my_table WHERE status = 'active'"
    res = validate_read_only(sql, allowed_tables={"my_table"})
    assert res == sql


def test_guard_allows_with_cte():
    sql = "WITH t AS (SELECT * FROM my_table) SELECT * FROM t"
    res = validate_read_only(sql, allowed_tables={"my_table", "t"})
    assert res == sql


def test_guard_allows_semicolon_inside_string_literal():
    sql = "SELECT * FROM my_table WHERE text = 'a;b;c'"
    res = validate_read_only(sql, allowed_tables={"my_table"})
    assert "a;b;c" in res


def test_guard_rejects_multi_statement():
    sql = "SELECT 1 FROM my_table; SELECT 2"
    with pytest.raises(SqlGuardError, match="Only one statement is allowed"):
        validate_read_only(sql)


def test_guard_rejects_destructive_keywords():
    queries = [
        "DROP TABLE my_table",
        "DELETE FROM my_table WHERE id = 1",
        "INSERT INTO my_table VALUES (1)",
        "CREATE TABLE fake (id INT)",
        "ALTER TABLE my_table ADD COLUMN x INT",
    ]
    for qry in queries:
        with pytest.raises(SqlGuardError):
            validate_read_only(qry)


def test_guard_rejects_unregistered_tables():
    sql = "SELECT * FROM secret_table"
    with pytest.raises(SqlGuardError, match="Table not in the verified model registry"):
        validate_read_only(sql, allowed_tables={"allowed_table"})
