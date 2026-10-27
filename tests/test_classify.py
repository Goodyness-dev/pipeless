from __future__ import annotations
from app.zsql.classify import (
    classify_statement,
    statement_target,
    READ,
    CREATE,
    CACHE,
    DESTRUCTIVE,
    ACCESS,
    EXPORT,
)


def test_classify_statements():
    assert classify_statement("SELECT * FROM table") == READ
    assert classify_statement("WITH cte AS (SELECT 1) SELECT * FROM cte") == READ
    assert classify_statement("SHOW LIGHTNING DATABASES") == READ
    assert classify_statement("DESCRIBE DATASOURCE TABLE db.tbl") == READ
    assert classify_statement("RUN DQ my_rule TABLE db.tbl") == READ
    assert classify_statement("CREATE LIGHTNING DATABASE my_db") == CREATE
    assert classify_statement("COMPILE USL my_usl") == CREATE
    assert classify_statement("ACTIVATE USL TABLE ns.usl.tbl") == CREATE
    assert classify_statement("CACHE TABLE db.tbl") == CACHE
    assert classify_statement("DROP LIGHTNING DATABASE my_db") == DESTRUCTIVE
    assert classify_statement("GRANT SELECT ON USL TABLE ns.tbl TO ROLE 'r'") == ACCESS
    assert classify_statement("MATERIALIZE TABLE ns.tbl") == EXPORT


def test_statement_targets():
    target = statement_target("CREATE LIGHTNING DATABASE NY_TAXI DESCRIBE BY 'taxi data'")
    assert target is not None
    assert target[0] == "LIGHTNING_DATABASE"
    assert target[1] == ("NY_TAXI",)

    target = statement_target("CREATE LIGHTNING REST TABLE raw_t FROM my_db REQUEST(...)")
    assert target is not None
    assert target[0] == "REST_TABLE"
    assert target[1] == ("raw_t", "my_db")

    target = statement_target("CACHE TABLE my_db.raw_t")
    assert target is not None
    assert target[0] == "CACHE_TABLE"
    assert target[1] == ("my_db.raw_t",)

    target = statement_target("CREATE SCHEMASTORE VIEW v WITH CONTAINER c AS SELECT 1")
    assert target is not None
    assert target[0] == "VIEW"
    assert target[1] == ("v", "c")
