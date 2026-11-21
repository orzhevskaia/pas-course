import csv
import io
import logging
import uuid
from contextlib import contextmanager
import psycopg2
from psycopg2.extras import Json, RealDictCursor
from .config import DATABASE_URL, ROOT

log = logging.getLogger(__name__)


def connect():
    return psycopg2.connect(DATABASE_URL)


def migrate():
    with connect() as conn, conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS meta")
        cur.execute(
            "CREATE TABLE IF NOT EXISTS meta.schema_migrations(name text PRIMARY KEY, applied_at timestamptz DEFAULT now())"
        )
        for path in sorted((ROOT / "sql/migrations").glob("*.sql")):
            cur.execute("SELECT 1 FROM meta.schema_migrations WHERE name=%s", (path.name,))
            if not cur.fetchone():
                cur.execute(path.read_text())
                cur.execute("INSERT INTO meta.schema_migrations(name) VALUES(%s)", (path.name,))
                log.info("Migration %s", path.name)


@contextmanager
def run(stage, logical_date, config=None):
    run_id = str(uuid.uuid4())
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO meta.runs(run_id,stage,logical_date,config,status) VALUES(%s,%s,%s,%s,'running')",
            (run_id, stage, logical_date, Json(config or {})),
        )
    stats = {}
    log.info("START %s %s", stage, run_id)
    try:
        yield run_id, stats
    except Exception as exc:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE meta.runs SET status='failed',ended_at=now(),error=%s,stats=%s WHERE run_id=%s",
                (str(exc)[:4000], Json(stats), run_id),
            )
        log.exception("FAILED %s", stage)
        raise
    else:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE meta.runs SET status='success',ended_at=now(),stats=%s WHERE run_id=%s",
                (Json(stats), run_id),
            )
        log.info("SUCCESS %s %s", stage, stats)


def copy_rows(cur, table, columns, rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in rows:
        writer.writerow(["\\N" if v is None else v for v in row])
    buf.seek(0)
    cur.copy_expert(
        f"COPY {table} ({','.join(columns)}) FROM STDIN WITH (FORMAT CSV, NULL '\\N')", buf
    )


def fetch(sql, params=None):
    with connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
