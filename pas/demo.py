"""Executable defence scenario. Synthetic defects never enter the business facts."""

import json
import datetime as dt
import pandas as pd
from .db import connect, run, fetch
from .config import ARTIFACTS
from .ingest import ingest_sales, request_weather
from .quality import validate_sales, record_checks, quarantine, silver_checks
from .scd import apply_version


def counts():
    return fetch("""SELECT (SELECT count(*) FROM bronze.sales) bronze_sales,
     (SELECT count(*) FROM silver.sales_daily) silver_sales,
     (SELECT count(*) FROM meta.batches WHERE source='sales_csv') sales_batches""")[0]


def demonstrate(as_of):
    result = {}
    before = counts()
    ingest_sales(as_of)
    after = counts()
    assert before == after, "Repeated ingestion changed business data"
    result["idempotent_sales"] = {"passed": True, "before": before, "after": after}
    with run("demonstration_quality", as_of, {"synthetic": True}) as (rid, stats):
        sample = dict(
            Store=1,
            Date=as_of,
            DayOfWeek=pd.Timestamp(as_of).dayofweek + 1,
            Sales=100,
            Customers=10,
            Open=1,
            Promo=0,
            StateHoliday="0",
            SchoolHoliday=0,
        )
        bad = pd.DataFrame(
            [
                sample,
                {**sample, "Sales": -1},
                {**sample, "Store": 99999},
                {**sample, "Sales": "broken"},
                {**sample, "Date": "bad-date"},
            ]
        )
        valid, rejected, checks = validate_sales(bad, set(range(1, 1116)))
        record_checks(rid, "synthetic_demo", checks)
        quarantine(rid, "synthetic_demo", rejected)
        assert len(rejected) >= 4
        stats.update(rejected=len(rejected), valid=len(valid), business_rows_inserted=0)
        result["quarantine"] = {"passed": True, **stats}
    try:
        with run("demonstration_failure", as_of, {"synthetic": True}) as (rid, stats):
            raise RuntimeError(
                "Intentional defence scenario: simulated source timeout; business data unchanged"
            )
    except RuntimeError:
        status = fetch("SELECT status,error FROM meta.runs WHERE run_id=%s", (rid,))[0]
        assert status["status"] == "failed"
        result["failure_log"] = {"passed": True, **status}

    class Response:
        status_code = 429
        headers = {"Retry-After": "0"}

    class RateLimited:
        calls = 0

        def get(self, *args, **kwargs):
            self.calls += 1
            return Response()

    fake = RateLimited()
    try:
        request_weather(fake, {}, sleep=lambda _: None)
        raise AssertionError("Rate limit must fail after bounded retries")
    except RuntimeError:
        assert fake.calls == 4
        result["rate_limit"] = {"passed": True, "attempts": fake.calls}
    with run("demonstration_scd2", as_of, {"synthetic": True, "effective_date": "2015-08-01"}) as (
        rid,
        stats,
    ):
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT attributes FROM silver.store_versions WHERE store_id=1 ORDER BY valid_from LIMIT 1"
            )
            attrs = cur.fetchone()[0]
            changed = {**attrs, "Assortment": "demo_changed", "demo": True}
            first = apply_version(
                cur, 1, changed, "2015-08-01", rid, "synthetic demo; not a real Rossmann change"
            )
            second = apply_version(cur, 1, changed, "2015-08-01", rid, "synthetic demo replay")
            assert second is False
            cur.execute("SELECT count(*) FROM silver.store_versions WHERE store_id=1")
            n = cur.fetchone()[0]
            assert n == 2
            stats.update(created_this_time=first, versions=n, replay_created=second)
            result["scd2"] = {"passed": True, **stats}
    with run("quality_gate", as_of) as (rid, stats):
        stats["checks"] = len(silver_checks(rid, as_of))
    result["business_data_unchanged"] = counts() == before
    try:
        with run(
            "demonstration_critical_gate", as_of, {"synthetic": True, "expected": "freshness block"}
        ) as (rid, stats):
            next_day = str(dt.date.fromisoformat(as_of) + dt.timedelta(days=1))
            silver_checks(rid, next_day)
    except ValueError as exc:
        result["critical_gate"] = {"passed": True, "blocked": True, "reason": str(exc)}
    else:
        raise AssertionError("Critical freshness gate did not block publication")
    ARTIFACTS.mkdir(exist_ok=True)
    (ARTIFACTS / "demo_evidence.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
