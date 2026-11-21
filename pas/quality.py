"""Quality policy: invalid rows are quarantined; critical Silver errors block publication."""

import hashlib
import json
import numpy as np
import pandas as pd
from psycopg2.extras import Json, execute_values
from .db import connect

REQUIRED = [
    "Store",
    "DayOfWeek",
    "Date",
    "Sales",
    "Customers",
    "Open",
    "Promo",
    "StateHoliday",
    "SchoolHoliday",
]


def digest(value):
    if not isinstance(value, str):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(value.encode()).hexdigest()


def record_checks(run_id, layer, checks):
    with connect() as conn, conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO meta.dq_results
          (run_id,layer,rule,kind,severity,failed_count,checked_count,passed,details) VALUES %s""",
            [
                (
                    run_id,
                    layer,
                    c["rule"],
                    c["kind"],
                    c.get("severity", "error"),
                    int(c["failed"]),
                    int(c["total"]),
                    c["failed"] == 0,
                    Json(c.get("details", {})),
                )
                for c in checks
            ],
        )


def validate_sales(frame, store_ids):
    missing = set(REQUIRED) - set(frame.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")
    raw = frame.copy()
    parsed = frame.copy()
    reasons = pd.Series("", index=frame.index)
    checks = []

    def add(rule, kind, bad, severity="error"):
        bad = bad.fillna(True)
        checks.append(
            dict(rule=rule, kind=kind, severity=severity, failed=int(bad.sum()), total=len(raw))
        )
        if severity == "error":
            reasons.loc[bad] += rule + ";"

    add("required_values", "completeness", raw[REQUIRED].isna().any(axis=1))
    for col in ["Store", "DayOfWeek", "Sales", "Customers", "Open", "Promo", "SchoolHoliday"]:
        parsed[col] = pd.to_numeric(raw[col], errors="coerce")
        bad = parsed[col].isna() | ~np.isfinite(parsed[col])
        if col != "Sales":
            bad |= parsed[col].mod(1).ne(0)
        add("type_" + col, "types", bad)
    parsed["Date"] = pd.to_datetime(raw["Date"], format="%Y-%m-%d", errors="coerce")
    add("date_type", "types", parsed["Date"].isna())
    add("nonnegative_sales_customers", "ranges", (parsed["Sales"] < 0) | (parsed["Customers"] < 0))
    add("weekday_range", "ranges", ~parsed["DayOfWeek"].between(1, 7))
    add(
        "weekday_consistency",
        "allowed_values",
        parsed["DayOfWeek"].ne(parsed["Date"].dt.dayofweek + 1),
    )
    add(
        "boolean_domains",
        "allowed_values",
        ~parsed[["Open", "Promo", "SchoolHoliday"]].isin([0, 1]).all(axis=1),
    )
    parsed["StateHoliday"] = raw["StateHoliday"].astype(str).str.lower()
    add("holiday_domain", "allowed_values", ~parsed["StateHoliday"].isin(["0", "a", "b", "c"]))
    add("store_reference", "referential_integrity", ~parsed["Store"].isin(store_ids))
    # Deterministic correction policy: last row in the supplied source revision wins.
    add("duplicate_business_key", "uniqueness", parsed.duplicated(["Store", "Date"], keep="last"))
    add(
        "open_zero_sales",
        "allowed_values",
        (parsed["Open"] == 1) & (parsed["Sales"] == 0),
        "warning",
    )
    rejected = raw.loc[reasons.ne("")].copy()
    rejected["_reason"] = reasons[reasons.ne("")]
    valid = parsed.loc[reasons.eq("")].copy()
    return valid, rejected, checks


def quarantine(run_id, source, rejected):
    if rejected.empty:
        return
    values = []
    for record in json.loads(rejected.to_json(orient="records", date_format="iso")):
        reason = record.pop("_reason")
        values.append((source, digest(record), Json(record), reason, run_id))
    with connect() as conn, conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO meta.quarantine(source,record_hash,payload,reason,run_id)
         VALUES %s ON CONFLICT(source,record_hash,reason) DO NOTHING""",
            values,
        )


def silver_checks(run_id, as_of):
    queries = [
        (
            "sales_nonempty",
            "completeness",
            "SELECT CASE WHEN count(*)=0 THEN 1 ELSE 0 END,count(*) FROM silver.sales_daily",
            "error",
        ),
        (
            "sales_unique",
            "uniqueness",
            "SELECT count(*)-count(DISTINCT (store_id,date)),count(*) FROM silver.sales_daily",
            "error",
        ),
        (
            "sales_range",
            "ranges",
            "SELECT count(*) FILTER(WHERE sales<0 OR customers<0),count(*) FROM silver.sales_daily",
            "error",
        ),
        (
            "sales_reference",
            "referential_integrity",
            "SELECT count(*) FILTER(WHERE st.store_id IS NULL),count(*) FROM silver.sales_daily s LEFT JOIN silver.stores st USING(store_id)",
            "error",
        ),
        (
            "sales_boolean",
            "types",
            "SELECT count(*) FILTER(WHERE is_open IS NULL OR promo IS NULL),count(*) FROM silver.sales_daily",
            "error",
        ),
        (
            "sales_weekday",
            "allowed_values",
            "SELECT count(*) FILTER(WHERE day_of_week<>extract(isodow FROM date)),count(*) FROM silver.sales_daily",
            "error",
        ),
        (
            "sales_freshness",
            "freshness",
            "SELECT CASE WHEN max(date)=DATE %s THEN 0 ELSE 1 END,count(*) FROM silver.sales_daily",
            "error",
        ),
        (
            "scd_overlap",
            "uniqueness",
            "SELECT count(*),(SELECT count(*) FROM silver.store_versions) FROM silver.store_versions a JOIN silver.store_versions b ON a.store_id=b.store_id AND a.version_id<b.version_id AND daterange(a.valid_from,a.valid_to,'[)') && daterange(b.valid_from,b.valid_to,'[)')",
            "error",
        ),
        (
            "weather_coverage",
            "completeness",
            "SELECT count(*) FILTER(WHERE w.region IS NULL OR w.temperature IS NULL),count(*) FROM silver.sales_daily s JOIN silver.stores st USING(store_id) LEFT JOIN silver.weather_daily w ON w.region=st.region AND w.date=s.date",
            "warning",
        ),
        (
            "historical_store_coverage",
            "referential_integrity",
            "SELECT count(*) FILTER(WHERE version_id IS NULL),count(*) FROM gold.sales_enriched",
            "error",
        ),
        (
            "source_record_lineage",
            "referential_integrity",
            "SELECT count(*) FILTER(WHERE source_row_hash IS NULL),count(*) FROM silver.sales_daily",
            "error",
        ),
    ]
    checks = []
    with connect() as conn, conn.cursor() as cur:
        for name, kind, sql, severity in queries:
            cur.execute(sql, (as_of,) if "%s" in sql else None)
            bad, total = cur.fetchone()
            checks.append(
                dict(
                    rule=name,
                    kind=kind,
                    severity=severity,
                    failed=bad,
                    total=total,
                    details={"logical_date": str(as_of)},
                )
            )
    record_checks(run_id, "silver", checks)
    critical = [c["rule"] for c in checks if c["failed"] and c["severity"] == "error"]
    if critical:
        raise ValueError(f"Publication blocked by DQ: {critical}")
    return checks
