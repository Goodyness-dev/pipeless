from __future__ import annotations
import pytest
from app.onboarding.ddl import (
    safe_desc,
    ident,
    create_database,
    create_rest_table,
    cache_table,
    create_container,
    create_view,
)


def test_safe_desc():
    raw = "My test: (database) / with [illegal] characters!"
    cleaned = safe_desc(raw)
    assert ":" not in cleaned
    assert "(" not in cleaned
    assert ")" not in cleaned
    assert "/" not in cleaned
    assert "[" not in cleaned
    assert "]" not in cleaned
    assert "My test database with illegal characters" in cleaned


def test_ident_valid():
    assert ident("my_table_123") == "my_table_123"
    assert ident("DB_NAME") == "DB_NAME"


def test_ident_invalid():
    with pytest.raises(ValueError):
        ident("table; DROP DATABASE")
    with pytest.raises(ValueError):
        ident("table name with spaces")
    with pytest.raises(ValueError):
        ident("table-name-with-hyphens")


def test_create_database_ddl():
    ddl = create_database("ny_taxi", "New York Taxi records (2026)")
    assert ddl.startswith("CREATE LIGHTNING DATABASE ny_taxi DESCRIBE BY")
    assert "New York Taxi records 2026" in ddl


def test_create_rest_table_ddl():
    ddl = create_rest_table("raw_api", "my_db", "https://api.example.com/data", "pipeless test@example.com")
    assert "CREATE LIGHTNING REST TABLE raw_api FROM my_db" in ddl
    assert 'endpoint "https://api.example.com/data"' in ddl
    assert 'user-agent "pipeless test@example.com"' in ddl


def test_cache_table_ddl():
    assert cache_table("my_db", "raw_api") == "CACHE TABLE my_db.raw_api"


def test_create_view_ddl():
    ddl = create_view("v_items", "c_container", "SELECT * FROM my_db.raw_api;")
    assert ddl.startswith("CREATE SCHEMASTORE VIEW v_items WITH CONTAINER c_container AS\nSELECT * FROM my_db.raw_api")
