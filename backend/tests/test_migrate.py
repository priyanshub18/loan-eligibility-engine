"""Schema splitting used by the migrate Lambda."""
from src.handlers.migrate import split_statements


def test_migrate_split_keeps_plpgsql_bodies_whole():
    sql = "CREATE TABLE a (x int);\nCREATE FUNCTION f() RETURNS trigger AS $$\nBEGIN\n  RETURN NEW;\nEND\n$$;\n-- comment only;\nDROP TABLE b;"
    stmts = split_statements(sql)
    assert len(stmts) == 3
    assert stmts[1].startswith("CREATE FUNCTION") and "RETURN NEW;" in stmts[1]
