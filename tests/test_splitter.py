from __future__ import annotations
from app.zsql.splitter import split_statements


def test_split_simple_statements():
    sql = "SELECT 1; SELECT 2; SELECT 3;"
    stmts = split_statements(sql)
    assert stmts == ["SELECT 1", "SELECT 2", "SELECT 3"]


def test_semicolon_inside_quotes():
    sql = "SELECT 'hello; world' AS greeting; SELECT \"another; test\" AS x;"
    stmts = split_statements(sql)
    assert len(stmts) == 2
    assert stmts[0] == "SELECT 'hello; world' AS greeting"
    assert stmts[1] == 'SELECT "another; test" AS x'


def test_comments_dropped():
    sql = """
    -- This is a comment
    SELECT 1;
    /* Block comment
       across multiple lines;
    */
    SELECT 2;
    """
    stmts = split_statements(sql)
    assert len(stmts) == 2
    assert stmts[0] == "SELECT 1"
    assert stmts[1] == "SELECT 2"


def test_compile_usl_with_multiple_create_tables():
    sql = """
    COMPILE USL IF NOT EXISTS test_usl DEPLOY NAMESPACE lightning.metastore.demo
    DDL
      CREATE TABLE table1 (id INT, name STRING);
      CREATE TABLE table2 (id INT, ref_id INT);
      CREATE TABLE table3 (code STRING, desc STRING);
    ;
    """
    stmts = split_statements(sql)
    assert len(stmts) == 1
    assert "table1" in stmts[0]
    assert "table2" in stmts[0]
    assert "table3" in stmts[0]
