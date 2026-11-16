import uuid
import pytest
from pas.db import connect, run, fetch
from pas.scd import apply_version


@pytest.fixture
def db():
    try:
        conn = connect()
    except Exception:
        pytest.skip("Database unavailable; run make up and make run for integration tests")
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('silver.stores')")
        if cur.fetchone()[0] is None:
            conn.close()
            pytest.skip("Initialize database first")
        cur.execute("SELECT count(*) FROM silver.stores")
        if cur.fetchone()[0] == 0:
            conn.close()
            pytest.skip("Ingest stores first")
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def test_scd_intervals_and_repeat_are_atomic(db):
    rid = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO meta.runs(run_id,stage,logical_date,status) VALUES(%s,'test','2015-07-31','running')",
            (rid,),
        )
        cur.execute("SELECT store_id FROM silver.stores ORDER BY store_id DESC LIMIT 1")
        sid = cur.fetchone()[0]
        cur.execute(
            "SELECT attributes FROM silver.store_versions WHERE store_id=%s ORDER BY valid_from DESC LIMIT 1",
            (sid,),
        )
        attrs = cur.fetchone()[0]
        new = {**attrs, "test_marker": True}
        assert apply_version(cur, sid, new, "2090-01-01", rid, "test")
        assert not apply_version(cur, sid, new, "2090-01-01", rid, "test repeat")
        cur.execute(
            "SELECT valid_from,valid_to FROM silver.store_versions WHERE store_id=%s ORDER BY valid_from",
            (sid,),
        )
        versions = cur.fetchall()
        assert versions[-2][1] == versions[-1][0]
        cur.execute(
            "SELECT count(*) FROM silver.store_versions WHERE store_id=%s AND %s::date>=valid_from AND %s::date<valid_to",
            (sid, "2090-01-01", "2090-01-01"),
        )
        assert cur.fetchone()[0] == 1


def test_failure_survives_transaction_rollback(db):
    with pytest.raises(RuntimeError):
        with run("test_intentional_failure", "2015-07-31") as (rid, stats):
            raise RuntimeError("Expected integration test failure")
    row = fetch("SELECT status,error FROM meta.runs WHERE run_id=%s", (rid,))[0]
    assert row["status"] == "failed" and row["error"] == "Expected integration test failure"


def test_historical_join_keeps_grain(db):
    with db.cursor() as cur:
        cur.execute(
            "SELECT (SELECT count(*) FROM silver.sales_daily),(SELECT count(*) FROM gold.sales_enriched)"
        )
        a, b = cur.fetchone()
        assert a == b
