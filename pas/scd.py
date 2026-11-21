import datetime as dt
from psycopg2.extras import Json
from .quality import digest


def apply_version(cur, store_id, attributes, effective_date, run_id, reason):
    """Close/open SCD2 atomically. Out-of-order changes need explicit backfill."""
    effective_date = dt.date.fromisoformat(str(effective_date))
    fingerprint = digest(attributes)
    cur.execute(
        "SELECT version_id,attribute_hash,valid_from FROM silver.store_versions WHERE store_id=%s AND valid_to='9999-12-31' FOR UPDATE",
        (store_id,),
    )
    previous = cur.fetchone()
    if previous and previous[1] == fingerprint:
        return False
    if previous:
        if effective_date <= previous[2]:
            raise ValueError(
                "SCD2 effective date must follow the current version start; use explicit historical backfill"
            )
        cur.execute(
            "UPDATE silver.store_versions SET valid_to=%s WHERE version_id=%s",
            (effective_date, previous[0]),
        )
    cur.execute(
        """INSERT INTO silver.store_versions(store_id,attributes,attribute_hash,valid_from,run_id,reason)
     VALUES(%s,%s,%s,%s,%s,%s)""",
        (store_id, Json(attributes), fingerprint, effective_date, run_id, reason),
    )
    return True
