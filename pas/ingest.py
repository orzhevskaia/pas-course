import datetime as dt
import json
import logging
import os
import time
import hashlib
import pandas as pd
import requests
from psycopg2.extras import execute_values, Json
from .config import DATA
from .db import connect, copy_rows, run
from .quality import digest, validate_sales, record_checks, quarantine
from .scd import apply_version

log = logging.getLogger(__name__)


def batch(cur, source, key, checksum, run_id, count):
    cur.execute(
        """INSERT INTO meta.batches(source,partition_key,checksum,run_id,rows_count)
     VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING batch_id""",
        (source, key, checksum, run_id, count),
    )
    row = cur.fetchone()
    return row[0] if row else None


def ingest_stores(as_of):
    with run(
        "ingest_stores", as_of, {"path": "data/store.csv", "geography": "synthetic fixed mapping"}
    ) as (run_id, stats):
        path = DATA / "store.csv"
        mapping = DATA / "store_geo_mapping.csv"
        df = pd.read_csv(path).merge(
            pd.read_csv(mapping), left_on="Store", right_on="store_id", validate="one_to_one"
        )
        if len(df) != pd.read_csv(path).shape[0] or df["Store"].duplicated().any():
            raise ValueError("Incomplete or duplicate geography mapping")
        checksum = hashlib.sha256(path.read_bytes() + mapping.read_bytes()).hexdigest()
        with connect() as conn, conn.cursor() as cur:
            bid = batch(cur, "stores_csv", "snapshot", checksum, run_id, len(df))
            if bid is None:
                stats["skipped"] = len(df)
                return
            payloads = json.loads(df.to_json(orient="records"))
            for p in payloads:
                sid = int(p["Store"])
                cur.execute(
                    "INSERT INTO bronze.stores(row_hash,store_id,payload,batch_id) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (digest(p), sid, Json(p), bid),
                )
                cur.execute(
                    """INSERT INTO silver.stores(store_id,store_type,assortment,competition_distance,competition_month,competition_year,
                 promo2,promo_week,promo_year,promo_interval,region,latitude,longitude,batch_id)
                 VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                 ON CONFLICT(store_id) DO UPDATE SET store_type=excluded.store_type,assortment=excluded.assortment,
                 competition_distance=excluded.competition_distance,competition_month=excluded.competition_month,
                 competition_year=excluded.competition_year,promo2=excluded.promo2,promo_week=excluded.promo_week,
                 promo_year=excluded.promo_year,promo_interval=excluded.promo_interval,region=excluded.region,
                 latitude=excluded.latitude,longitude=excluded.longitude,batch_id=excluded.batch_id""",
                    (
                        sid,
                        p["StoreType"],
                        p["Assortment"],
                        p["CompetitionDistance"],
                        p["CompetitionOpenSinceMonth"],
                        p["CompetitionOpenSinceYear"],
                        bool(p["Promo2"]),
                        p["Promo2SinceWeek"],
                        p["Promo2SinceYear"],
                        p["PromoInterval"],
                        p["region_name"],
                        p["latitude"],
                        p["longitude"],
                        bid,
                    ),
                )
                attrs = {
                    k: p[k]
                    for k in [
                        "StoreType",
                        "Assortment",
                        "CompetitionDistance",
                        "Promo2",
                        "PromoInterval",
                    ]
                }
                cur.execute("SELECT 1 FROM silver.store_versions WHERE store_id=%s", (sid,))
                effective = as_of if cur.fetchone() else "2013-01-01"
                apply_version(
                    cur,
                    sid,
                    attrs,
                    effective,
                    run_id,
                    "source snapshot; initial attributes assumed valid from start of history",
                )
            stats["loaded"] = len(df)


def ingest_sales(as_of):
    with run(
        "ingest_sales",
        as_of,
        {
            "source": "data/train.csv",
            "partition": "calendar month",
            "correction_policy": "last row of revision",
        },
    ) as (run_id, stats):
        # Reading a local source manifest is cheap; completed partitions are never reinserted.
        df = pd.read_csv(DATA / "train.csv", dtype={"StateHoliday": str})
        df = df.loc[df.Date <= as_of].copy()
        if df.empty:
            raise ValueError("No sales up to logical date")
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT store_id FROM silver.stores")
            ids = {x[0] for x in cur.fetchall()}
        loaded = skipped = rejected_count = 0
        for key, part in df.groupby(df.Date.str[:7], sort=True):
            checksum = digest(part.to_csv(index=False))
            with connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM meta.batches WHERE source=%s AND partition_key=%s AND checksum=%s",
                    ("sales_csv", key, checksum),
                )
                if cur.fetchone():
                    skipped += len(part)
                    continue
            valid, rejected, checks = validate_sales(part, ids)
            record_checks(run_id, "bronze_sales:" + key, checks)
            quarantine(run_id, "sales_csv", rejected)
            rejected_count += len(rejected)
            with connect() as conn, conn.cursor() as cur:
                bid = batch(cur, "sales_csv", key, checksum, run_id, len(part))
                if bid is None:
                    skipped += len(part)
                    continue
                payloads = json.loads(part.to_json(orient="records"))
                source_hash = {index: digest(p) for index, p in zip(part.index, payloads)}
                cur.execute(
                    "CREATE TEMP TABLE incoming_raw(LIKE bronze.sales INCLUDING DEFAULTS) ON COMMIT DROP"
                )
                copy_rows(
                    cur,
                    "incoming_raw",
                    ["row_hash", "store_id", "business_date", "payload", "batch_id"],
                    (
                        (digest(p), int(p["Store"]), p["Date"], json.dumps(p), bid)
                        for p in payloads
                        if str(p.get("Store", "")).isdigit()
                        and pd.notna(
                            pd.to_datetime(p.get("Date"), format="%Y-%m-%d", errors="coerce")
                        )
                    ),
                )
                cur.execute(
                    "INSERT INTO bronze.sales SELECT * FROM incoming_raw ON CONFLICT DO NOTHING"
                )
                cur.execute(
                    "CREATE TEMP TABLE incoming_sales(LIKE silver.sales_daily INCLUDING DEFAULTS) ON COMMIT DROP"
                )
                copy_rows(
                    cur,
                    "incoming_sales",
                    [
                        "store_id",
                        "date",
                        "day_of_week",
                        "sales",
                        "customers",
                        "is_open",
                        "promo",
                        "state_holiday",
                        "school_holiday",
                        "batch_id",
                        "source_row_hash",
                    ],
                    (
                        (
                            int(r.Store),
                            r.Date.date(),
                            int(r.DayOfWeek),
                            float(r.Sales),
                            int(r.Customers),
                            bool(r.Open),
                            bool(r.Promo),
                            str(r.StateHoliday),
                            bool(r.SchoolHoliday),
                            bid,
                            source_hash[r.Index],
                        )
                        for r in valid.itertuples()
                    ),
                )
                cur.execute("""INSERT INTO silver.sales_daily SELECT * FROM incoming_sales
                 ON CONFLICT(store_id,date) DO UPDATE SET day_of_week=excluded.day_of_week,sales=excluded.sales,
                 customers=excluded.customers,is_open=excluded.is_open,promo=excluded.promo,state_holiday=excluded.state_holiday,
                 school_holiday=excluded.school_holiday,batch_id=excluded.batch_id,source_row_hash=excluded.source_row_hash""")
            loaded += len(valid)
            log.info("Sales %s: %s valid, %s quarantine", key, len(valid), len(rejected))
        stats.update(loaded=loaded, skipped=skipped, rejected=rejected_count)


def request_weather(session, params, sleep=time.sleep):
    """Finite retries; Retry-After honoured with a bounded demo wait."""
    error = None
    for attempt in range(4):
        try:
            response = session.get(
                "https://archive-api.open-meteo.com/v1/archive", params=params, timeout=(10, 60)
            )
            if response.status_code == 429 or response.status_code >= 500:
                error = RuntimeError(f"Weather HTTP {response.status_code}")
                delay = min(30, float(response.headers.get("Retry-After", 2**attempt)))
                if attempt < 3:
                    sleep(delay)
                continue
            response.raise_for_status()
            result = response.json()
            daily = result.get("daily", {})
            times = daily.get("time", [])
            expected = len(pd.date_range(params["start_date"], params["end_date"]))
            if (
                len(times) != expected
                or len(set(times)) != expected
                or times[0] != params["start_date"]
                or times[-1] != params["end_date"]
            ):
                raise ValueError("Weather response has incomplete dates")
            if any(
                len(daily.get(k, [])) != expected
                for k in ["temperature_2m_mean", "precipitation_sum"]
            ):
                raise ValueError("Weather response has inconsistent arrays")
            return result
        except (requests.Timeout, requests.ConnectionError) as exc:
            error = exc
            if attempt < 3:
                sleep(2**attempt)
    raise RuntimeError(f"Weather unavailable after 4 attempts: {error}")


def cached_weather(cache, region, params):
    """Reuse a verified larger API interval for a smaller historical replay.

    The original request/response file remains unchanged; no request is fabricated.
    """
    for candidate in sorted(cache.glob(f"{region}_*.json")):
        envelope = json.loads(candidate.read_text())
        request = envelope["request"]
        same = all(
            request.get(k) == params[k] for k in ["latitude", "longitude", "daily", "timezone"]
        )
        if (
            same
            and request["start_date"] <= params["start_date"]
            and request["end_date"] >= params["end_date"]
        ):
            response = envelope["response"]
            times = response["daily"]["time"]
            wanted = list(
                pd.date_range(params["start_date"], params["end_date"]).strftime("%Y-%m-%d")
            )
            available = {day: i for i, day in enumerate(times)}
            if not all(day in available for day in wanted):
                continue
            indexes = [available[day] for day in wanted]
            sliced = {
                **response,
                "daily": {
                    key: [values[i] for i in indexes] for key, values in response["daily"].items()
                },
            }
            return sliced, candidate.name
    return None, None


def ingest_weather(as_of, mode=None):
    mode = mode or os.getenv("WEATHER_MODE", "auto")
    with run(
        "ingest_weather",
        as_of,
        {
            "api": "Open-Meteo archive",
            "mode": mode,
            "geography": "synthetic regions; no predictive claims",
        },
    ) as (run_id, stats):
        geo = (
            pd.read_csv(DATA / "store_geo_mapping.csv")
            .groupby("region_name")[["latitude", "longitude"]]
            .mean()
            .round(4)
        )
        cache = DATA / "weather_cache"
        cache.mkdir(exist_ok=True)
        loaded = skipped = 0
        with requests.Session() as session:
            for region, coords in geo.iterrows():
                with connect() as conn, conn.cursor() as cur:
                    cur.execute(
                        "SELECT date FROM silver.weather_daily WHERE region=%s AND date<=%s",
                        (region, as_of),
                    )
                    have = {r[0] for r in cur.fetchall()}
                all_days = pd.date_range("2013-01-01", as_of)
                missing = [day.date() for day in all_days if day.date() not in have]
                if not missing:
                    skipped += len(all_days)
                    continue
                # Contiguous missing intervals only, never refetch a fully loaded interval.
                groups = []
                for day in missing:
                    if not groups or day > groups[-1][-1] + dt.timedelta(days=1):
                        groups.append([day])
                    else:
                        groups[-1].append(day)
                for days in groups:
                    start, end = str(days[0]), str(days[-1])
                    params = {
                        "latitude": float(coords.latitude),
                        "longitude": float(coords.longitude),
                        "start_date": start,
                        "end_date": end,
                        "daily": "temperature_2m_mean,precipitation_sum",
                        "timezone": "Europe/Berlin",
                    }
                    path = cache / f"{region}_{start}_{end}.json"
                    from_cache = False
                    cached, cache_name = (
                        cached_weather(cache, region, params) if mode != "live" else (None, None)
                    )
                    if cached is not None:
                        result = cached
                        from_cache = True
                    elif mode == "offline":
                        raise FileNotFoundError(
                            f"No cached API response: {path.name}; run WEATHER_MODE=live once"
                        )
                    else:
                        result = request_weather(session, params)
                        envelope = {
                            "request": params,
                            "response": result,
                            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                            "source_url": "https://archive-api.open-meteo.com/v1/archive",
                        }
                        path.write_text(json.dumps(envelope, ensure_ascii=False))
                    daily = result["daily"]
                    frame = pd.DataFrame(
                        {
                            "date": daily["time"],
                            "temperature": daily["temperature_2m_mean"],
                            "precipitation": daily["precipitation_sum"],
                        }
                    )
                    bad = (
                        frame.temperature.isna()
                        | frame.precipitation.isna()
                        | (frame.precipitation < 0)
                    )
                    record_checks(
                        run_id,
                        "weather",
                        [
                            dict(
                                rule="weather_values",
                                kind="ranges",
                                severity="error",
                                failed=int(bad.sum()),
                                total=len(frame),
                            )
                        ],
                    )
                    if bad.any():
                        raise ValueError(f"Invalid weather response for {region}")
                    with connect() as conn, conn.cursor() as cur:
                        checksum = digest(result)
                        bid = batch(
                            cur,
                            "weather_api",
                            f"{region}:{start}:{end}",
                            checksum,
                            run_id,
                            len(frame),
                        )
                        if bid:
                            cur.execute(
                                "UPDATE meta.batches SET source_revision=%s WHERE batch_id=%s",
                                (cache_name if from_cache else path.name, bid),
                            )
                            raw = []
                            clean = []
                            for row in frame.itertuples():
                                p = {
                                    "region": region,
                                    "date": row.date,
                                    "temperature": row.temperature,
                                    "precipitation": row.precipitation,
                                }
                                raw.append((digest(p), region, row.date, Json(p), bid))
                                clean.append(
                                    (region, row.date, row.temperature, row.precipitation, bid)
                                )
                            execute_values(
                                cur,
                                "INSERT INTO bronze.weather VALUES %s ON CONFLICT DO NOTHING",
                                raw,
                            )
                            execute_values(
                                cur,
                                """INSERT INTO silver.weather_daily VALUES %s ON CONFLICT(region,date)
                             DO UPDATE SET temperature=excluded.temperature,precipitation=excluded.precipitation,batch_id=excluded.batch_id""",
                                clean,
                            )
                    loaded += len(frame)
                    log.info(
                        "Weather %s %s:%s (%s)",
                        region,
                        start,
                        end,
                        "verified cache" if from_cache else "live API",
                    )
        stats.update(loaded=loaded, skipped=skipped)
