import argparse
import json
import logging
from .config import AS_OF, HORIZON
from .db import migrate, run, connect, fetch
from .ingest import ingest_stores, ingest_sales, ingest_weather
from .quality import silver_checks
from .metadata import register_metadata


def pipeline(as_of, weather_mode=None, force=False):
    # Session-level advisory lock prevents competing pipelines publishing overlapping snapshots.
    lock = connect()
    try:
        with lock.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(7182601)")
            if not cur.fetchone()[0]:
                raise RuntimeError("Another pipeline is already running")
        migrate()
        with run("pipeline", as_of, {"horizon": HORIZON, "mode": "historical replay"}) as (
            rid,
            stats,
        ):
            register_metadata()
            ingest_stores(as_of)
            ingest_sales(as_of)
            ingest_weather(as_of, weather_mode)
            with run("quality_gate", as_of) as (qid, qstats):
                qstats["checks"] = len(silver_checks(qid, as_of))
            from .ml import train_and_forecast

            stats["model_run_id"] = train_and_forecast(as_of, force=force)
            from .report import generate

            generate(as_of)
    finally:
        lock.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Rossmann Forecast data lifecycle")
    parser.add_argument(
        "command",
        choices=["init", "run", "ingest", "quality", "forecast", "demo", "report", "status"],
    )
    parser.add_argument("--as-of", default=AS_OF)
    parser.add_argument("--weather-mode", choices=["auto", "live", "offline"])
    parser.add_argument(
        "--force", action="store_true", help="Explicitly create a new model experiment"
    )
    args = parser.parse_args()
    migrate()
    if args.command == "init":
        register_metadata()
    elif args.command == "run":
        pipeline(args.as_of, args.weather_mode, args.force)
    elif args.command == "ingest":
        ingest_stores(args.as_of)
        ingest_sales(args.as_of)
        ingest_weather(args.as_of, args.weather_mode)
    elif args.command == "quality":
        with run("quality_gate", args.as_of) as (rid, stats):
            stats["checks"] = len(silver_checks(rid, args.as_of))
    elif args.command == "forecast":
        with run("quality_gate", args.as_of) as (rid, stats):
            stats["checks"] = len(silver_checks(rid, args.as_of))
        from .ml import train_and_forecast

        train_and_forecast(args.as_of, force=args.force)
    elif args.command == "demo":
        from .demo import demonstrate

        demonstrate(args.as_of)
    elif args.command == "report":
        from .report import generate

        generate(args.as_of)
    elif args.command == "status":
        print(
            json.dumps(
                fetch(
                    "SELECT stage,status,logical_date,started_at,stats,error FROM meta.runs ORDER BY started_at DESC LIMIT 20"
                ),
                default=str,
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
