"""Verify increments and corrected source revisions in an isolated disposable database."""

import json
import uuid
import pandas as pd
from psycopg2 import sql
from psycopg2.extensions import parse_dsn, make_dsn
import pytest
import pas.db as db
import pas.ingest as ingestion
from pas.config import DATA, ARTIFACTS


def test_incremental_correction_and_record_lineage(tmp_path, monkeypatch):
    try:
        admin = db.connect()
    except Exception:
        pytest.skip("PostgreSQL unavailable")
    admin.autocommit = True
    name = "pas_test_" + uuid.uuid4().hex[:12]
    with admin.cursor() as cur:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    options = parse_dsn(db.DATABASE_URL)
    options["dbname"] = name
    monkeypatch.setattr(db, "DATABASE_URL", make_dsn(**options))
    monkeypatch.setattr(ingestion, "DATA", tmp_path)
    try:
        pd.read_csv(DATA / "store.csv").iloc[:1].to_csv(tmp_path / "store.csv", index=False)
        # Keep all fixed region coordinates so the real API cache is reusable without network access.
        pd.read_csv(DATA / "store_geo_mapping.csv").to_csv(
            tmp_path / "store_geo_mapping.csv", index=False
        )
        (tmp_path / "weather_cache").symlink_to(DATA / "weather_cache", target_is_directory=True)
        source = pd.read_csv(DATA / "train.csv", dtype={"StateHoliday": str})
        source = source[
            (source.Store == 1) & source.Date.between("2015-07-29", "2015-07-31")
        ].sort_values("Date")
        source.to_csv(tmp_path / "train.csv", index=False)
        db.migrate()
        ingestion.ingest_stores("2015-07-30")
        ingestion.ingest_sales("2015-07-30")
        ingestion.ingest_weather("2015-07-30", "offline")
        weather_before = db.fetch("SELECT count(*) n FROM silver.weather_daily")[0]["n"]

        def count():
            return db.fetch(
                "SELECT (SELECT count(*) FROM bronze.sales) raw,(SELECT count(*) FROM silver.sales_daily) clean"
            )[0]

        initial = count()
        assert initial == {"raw": 2, "clean": 2}
        ingestion.ingest_sales("2015-07-30")
        assert count() == initial
        ingestion.ingest_sales("2015-07-31")
        ingestion.ingest_weather("2015-07-31", "offline")
        weather_after = db.fetch("SELECT count(*) n FROM silver.weather_daily")[0]["n"]
        assert weather_after - weather_before == 20
        advanced = count()
        assert advanced == {"raw": 3, "clean": 3}
        # Unchanged raw hashes retain their original batch but every current fact remains traceable.
        trace = db.fetch(
            "SELECT count(*) n FROM silver.sales_daily s JOIN bronze.sales b ON b.row_hash=s.source_row_hash"
        )[0]["n"]
        assert trace == 3
        source.loc[source.Date == "2015-07-30", "Sales"] = 12345
        source.to_csv(tmp_path / "train.csv", index=False)
        ingestion.ingest_sales("2015-07-31")
        revised = count()
        assert revised == {"raw": 4, "clean": 3}
        value = db.fetch("SELECT sales FROM silver.sales_daily WHERE date='2015-07-30'")[0]["sales"]
        assert value == 12345
        result = {
            "passed": True,
            "isolated_database": True,
            "initial": initial,
            "next_partition": advanced,
            "correction": revised,
            "corrected_value": value,
            "record_lineage_rows": trace,
            "weather_offline_before": weather_before,
            "weather_offline_after": weather_after,
            "new_weather_rows": weather_after - weather_before,
        }
        ARTIFACTS.mkdir(exist_ok=True)
        (ARTIFACTS / "incremental_evidence.json").write_text(json.dumps(result, indent=2))
    finally:
        with admin.cursor() as cur:
            cur.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        admin.close()
