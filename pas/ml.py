"""Direct 42-day forecasts. Every target-derived feature is at least HORIZON days old."""

import hashlib
import json
import logging
import os
import platform
import uuid
import zipfile
import importlib.metadata
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from threadpoolctl import threadpool_limits
from psycopg2.extras import Json
from .config import ARTIFACTS, DATA, ROOT, HORIZON, SEED
from .db import connect, copy_rows, run

log = logging.getLogger(__name__)
MODEL_NAMES = ["Seasonal Naive", "HistGradientBoosting"]


def load_history(as_of):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT store_id,date,sales,is_open,promo,state_holiday,school_holiday
         FROM silver.sales_daily WHERE date<=%s ORDER BY store_id,date""",
            (as_of,),
        )
        frame = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
        cur.execute(
            "SELECT store_id,store_type,assortment,competition_distance FROM silver.stores ORDER BY store_id"
        )
        stores = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
    frame["date"] = pd.to_datetime(frame["date"])
    return frame, stores


def build_features(frame, stores, horizon=HORIZON):
    """Calendar reindexing prevents row shifts from crossing gaps in a time series."""
    frame = frame.sort_values(["store_id", "date"]).copy()
    dates = pd.date_range(frame.date.min(), frame.date.max())
    grid = pd.MultiIndex.from_product(
        [sorted(frame.store_id.unique()), dates], names=["store_id", "date"]
    )
    daily = frame.set_index(["store_id", "date"]).reindex(grid)
    grouped = daily["sales"].groupby(level="store_id", sort=False)
    x = frame[["store_id", "date"]].copy()
    idx = pd.MultiIndex.from_frame(x[["store_id", "date"]])
    for lag in [horizon, horizon + 7, horizon + 14, 364]:
        if lag < horizon:
            continue
        x[f"lag_{lag}"] = grouped.shift(lag).reindex(idx).to_numpy()
    # Rolling window ends on target_date-horizon, never inside the forecast horizon.
    shifted = grouped.shift(horizon)
    for window in [7, 28]:
        series = shifted.groupby(level="store_id", sort=False).transform(
            lambda s: s.rolling(window, min_periods=1).mean()
        )
        x[f"past_mean_{window}"] = series.reindex(idx).to_numpy()
    x["weekday"] = frame.date.dt.dayofweek.to_numpy()
    x["month"] = frame.date.dt.month.to_numpy()
    x["day_of_year"] = frame.date.dt.dayofyear.to_numpy()
    x["trend"] = (frame.date - pd.Timestamp("2013-01-01")).dt.days.to_numpy()
    x["promo"] = frame.promo.astype(int).to_numpy()
    x["is_open"] = frame.is_open.astype(int).to_numpy()
    x["school_holiday"] = frame.school_holiday.astype(int).to_numpy()
    x["state_holiday"] = (
        frame.state_holiday.map({"0": 0, "a": 1, "b": 2, "c": 3}).fillna(0).to_numpy()
    )
    # Snapshot store metadata is not used as a historical feature: observed effective dates are unavailable.
    # Store identity is known; quantitative sales history comes exclusively from time-safe lags.
    return (
        x.drop(columns="date").astype(float).replace([np.inf, -np.inf], np.nan).astype(np.float32)
    )


def seasonal_naive(history, targets, origin):
    """Repeat the last observed calendar week, recursively, with known closure override."""
    history = history.loc[history.date <= pd.Timestamp(origin)]
    lookup = history.set_index(["store_id", "date"]).sales
    dates = targets.date.copy()
    while (dates > pd.Timestamp(origin)).any():
        dates = dates.where(dates <= pd.Timestamp(origin), dates - pd.Timedelta(days=7))
    idx = pd.MultiIndex.from_arrays([targets.store_id.to_numpy(), dates.to_numpy()])
    fallback = history.groupby("store_id").sales.mean()
    prediction = pd.Series(lookup.reindex(idx).to_numpy(), index=targets.index)
    prediction = prediction.fillna(targets.store_id.map(fallback)).fillna(0).to_numpy()
    return np.where(targets.is_open.to_numpy(), prediction, 0).clip(0)


def metrics(actual, prediction):
    actual = np.asarray(actual, dtype=float)
    prediction = np.asarray(prediction, dtype=float)
    mask = actual > 0
    denom = np.abs(actual).sum()
    return {
        "mae": float(np.abs(actual - prediction).mean()),
        "wape": float(np.abs(actual - prediction).sum() / denom) if denom else None,
        "rmspe": float(np.sqrt(np.mean(((actual[mask] - prediction[mask]) / actual[mask]) ** 2)))
        if mask.any()
        else None,
        "n": len(actual),
    }


def future_schedule(history, as_of, horizon):
    origin = pd.Timestamp(as_of)
    idx = pd.MultiIndex.from_product(
        [
            sorted(history.store_id.unique()),
            pd.date_range(origin + pd.Timedelta(days=1), periods=horizon),
        ],
        names=["store_id", "date"],
    )
    future = idx.to_frame(index=False)
    planned = pd.read_csv(DATA / "test.csv", dtype={"StateHoliday": str})
    planned["date"] = pd.to_datetime(planned.Date)
    planned = planned.rename(
        columns={
            "Store": "store_id",
            "Open": "is_open",
            "Promo": "promo",
            "StateHoliday": "state_holiday",
            "SchoolHoliday": "school_holiday",
        }
    )
    future = future.merge(
        planned[["store_id", "date", "is_open", "promo", "state_holiday", "school_holiday"]],
        how="left",
        on=["store_id", "date"],
        validate="one_to_one",
    )
    recent = history.loc[history.date > origin - pd.Timedelta(days=84)].copy()
    recent["weekday"] = recent.date.dt.dayofweek
    open_pattern = recent.groupby(["store_id", "weekday"]).is_open.mean()
    keys = pd.MultiIndex.from_arrays([future.store_id, future.date.dt.dayofweek])
    assumed_open = open_pattern.reindex(keys).fillna(1).to_numpy() >= 0.5
    future["schedule_source"] = np.where(
        future.is_open.notna(), "Kaggle test planned scenario", "assumed calendar; promo=0"
    )
    future["is_open"] = future.is_open.where(
        future.is_open.notna(), pd.Series(assumed_open, index=future.index)
    ).astype(bool)
    future["promo"] = future.promo.fillna(0).astype(bool)
    future["state_holiday"] = future.state_holiday.fillna("0")
    future["school_holiday"] = future.school_holiday.fillna(0).astype(bool)
    future["sales"] = np.nan
    return future


def train_and_forecast(as_of, horizon=HORIZON, force=False):
    if horizon != 42:
        raise ValueError(
            "This validated prototype fixes horizon=42; changing it requires revalidation"
        )
    history, stores = load_history(as_of)
    if len(history) < 1000 or history.date.max() - history.date.min() < pd.Timedelta(days=500):
        raise ValueError(
            "At least 500 days of history required for three 42-day validation windows"
        )
    data_hash = hashlib.sha256(
        pd.util.hash_pandas_object(history, index=False).values.tobytes()
    ).hexdigest()
    code_hash = hashlib.sha256(
        b"".join(p.read_bytes() for p in sorted((ROOT / "pas").glob("*.py")))
    ).hexdigest()
    settings = {
        "horizon": horizon,
        "seed": SEED,
        "iterations": int(os.getenv("MODEL_ITERATIONS", "100")),
        "loss": "squared_error on log1p(Sales)",
        "lag_policy": "target_date minus >=42 days",
        "weather_used": False,
        "customers_used": False,
        "metadata_features_used": False,
        "known_future": "Open/Promo/holidays assumed planned; missing schedule uses explicit calendar scenario",
        "schedule_sha256": hashlib.sha256((DATA / "test.csv").read_bytes()).hexdigest(),
    }
    environment = {p: importlib.metadata.version(p) for p in ["numpy", "pandas", "scikit-learn"]}
    environment["python"] = platform.python_version()
    with run("forecast", as_of, settings) as (run_id, stats):
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT m.model_run_id FROM meta.model_runs m JOIN meta.runs r USING(run_id)
             WHERE m.origin=%s AND m.data_hash=%s AND m.code_hash=%s AND m.config=%s AND m.environment=%s AND r.status='success' """,
                (as_of, data_hash, code_hash, Json(settings), Json(environment)),
            )
            existing = cur.fetchone()
        if existing and not force:
            stats.update(skipped=True, model_run_id=str(existing[0]))
            return str(existing[0])
        mid = str(uuid.uuid4())
        folder = ARTIFACTS / mid
        folder.mkdir(parents=True, exist_ok=True)
        history.to_parquet(folder / "training_snapshot.parquet", index=False)
        with zipfile.ZipFile(folder / "source_code.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted((ROOT / "pas").glob("*.py")):
                bundle.write(path, path.relative_to(ROOT))
        origins = [pd.Timestamp(as_of) - pd.Timedelta(days=horizon * k) for k in [3, 2, 1]]
        feature_frame = build_features(history, stores, horizon)
        # Avoid fitting on early rows with no horizon-safe observations.
        observed = feature_frame[f"lag_{horizon}"].notna()
        rows = []
        all_metrics = []
        importance = []
        with threadpool_limits(limits=2):
            for i, origin in enumerate(origins):
                train = (history.date <= origin) & observed
                val = (history.date > origin) & (
                    history.date <= origin + pd.Timedelta(days=horizon)
                )
                model = HistGradientBoostingRegressor(
                    max_iter=settings["iterations"],
                    max_leaf_nodes=31,
                    learning_rate=0.10,
                    l2_regularization=5,
                    early_stopping=False,
                    random_state=SEED,
                )
                model.fit(feature_frame.loc[train], np.log1p(history.loc[train, "sales"]))
                actual = history.loc[val].copy()
                base = seasonal_naive(history, actual, origin)
                advanced = np.expm1(model.predict(feature_frame.loc[val])).clip(0)
                advanced = np.where(actual.is_open, advanced, 0)
                for name, pred in zip(MODEL_NAMES, [base, advanced]):
                    result = actual[["store_id", "date", "sales"]].rename(
                        columns={"sales": "actual"}
                    )
                    result["prediction"] = pred
                    result["model"] = name
                    result["origin"] = origin
                    rows.append(result)
                    for segment, part in [
                        ("all", result),
                        *[
                            (
                                f"horizon_{j + 1}_{j + 7}",
                                result[(result.date - origin).dt.days.between(j + 1, j + 7)],
                            )
                            for j in range(0, horizon, 7)
                        ],
                    ]:
                        m = metrics(part.actual, part.prediction)
                        all_metrics.append(
                            dict(model=name, origin=str(origin.date()), segment=segment, **m)
                        )
                    enriched = result.merge(stores[["store_id", "store_type"]], on="store_id")
                    for kind, part in enriched.groupby("store_type"):
                        all_metrics.append(
                            dict(
                                model=name,
                                origin=str(origin.date()),
                                segment="store_type_" + kind,
                                **metrics(part.actual, part.prediction),
                            )
                        )
                    log.info(
                        "Validation %s %s %s",
                        origin.date(),
                        name,
                        metrics(result.actual, result.prediction),
                    )
                if i == len(origins) - 1:
                    rng = np.random.default_rng(SEED)
                    sample = rng.choice(actual.index, size=min(5000, len(actual)), replace=False)
                    perm = permutation_importance(
                        model,
                        feature_frame.loc[sample],
                        np.log1p(history.loc[sample, "sales"]),
                        n_repeats=2,
                        random_state=SEED,
                        scoring="neg_mean_absolute_error",
                    )
                    importance = [
                        {"feature": name, "importance": float(value)}
                        for name, value in zip(feature_frame.columns, perm.importances_mean)
                    ]
            future = future_schedule(history, as_of, horizon)
            combined = (
                pd.concat([history, future.drop(columns="schedule_source")], ignore_index=True)
                .sort_values(["store_id", "date"])
                .reset_index(drop=True)
            )
            combined_features = build_features(combined, stores, horizon)
            train = (combined.date <= pd.Timestamp(as_of)) & combined_features[
                f"lag_{horizon}"
            ].notna()
            predict = combined.date > pd.Timestamp(as_of)
            final = HistGradientBoostingRegressor(
                max_iter=settings["iterations"],
                max_leaf_nodes=31,
                learning_rate=0.10,
                l2_regularization=5,
                early_stopping=False,
                random_state=SEED,
            )
            final.fit(combined_features.loc[train], np.log1p(combined.loc[train, "sales"]))
            future = future.sort_values(["store_id", "date"]).reset_index(drop=True)
            prediction = np.expm1(final.predict(combined_features.loc[predict])).clip(0)
            forecasts = []
            for name, pred in zip(
                MODEL_NAMES,
                [seasonal_naive(history, future, as_of), np.where(future.is_open, prediction, 0)],
            ):
                out = future[["store_id", "date", "schedule_source"]].copy()
                out["prediction"] = pred
                out["model"] = name
                out["origin"] = pd.Timestamp(as_of)
                forecasts.append(out)
        validations = pd.concat(rows, ignore_index=True)
        predictions = pd.concat(forecasts, ignore_index=True)
        # Aggregate per-store metrics across the three disjoint validation windows.
        for (name, sid), part in validations.groupby(["model", "store_id"]):
            all_metrics.append(
                dict(
                    model=name,
                    origin=as_of,
                    segment=f"store_{sid}",
                    **metrics(part.actual, part.prediction),
                )
            )
        summary = {
            "model_run_id": mid,
            "origin": as_of,
            "data_hash": data_hash,
            "code_hash": code_hash,
            "config": settings,
            "environment": environment,
            "validation": all_metrics[:2],
            "folds": [m for m in all_metrics if m["segment"] == "all"],
            "importance": sorted(importance, key=lambda v: -v["importance"]),
        }
        joblib.dump(
            {"model": final, "features": list(feature_frame.columns), "summary": summary},
            folder / "model.joblib",
        )
        (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        pd.DataFrame(all_metrics).to_csv(folder / "metrics.csv", index=False)
        predictions.to_csv(folder / "forecasts.csv", index=False)
        validations.to_parquet(folder / "validation.parquet", index=False)
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO meta.model_runs(model_run_id,run_id,origin,data_hash,code_hash,artifact_path,config,environment)
             VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    mid,
                    run_id,
                    as_of,
                    data_hash,
                    code_hash,
                    str(folder.relative_to(ROOT)),
                    Json(settings),
                    Json(environment),
                ),
            )
            cur.execute(
                """INSERT INTO meta.model_batches(model_run_id,batch_id)
             SELECT %s,batch_id FROM silver.sales_daily WHERE date<=%s GROUP BY batch_id""",
                (mid, as_of),
            )
            copy_rows(
                cur,
                "gold.validation_predictions",
                ["model_run_id", "model", "origin", "store_id", "date", "actual", "prediction"],
                (
                    (
                        mid,
                        r.model,
                        r.origin.date(),
                        int(r.store_id),
                        r.date.date(),
                        float(r.actual),
                        float(r.prediction),
                    )
                    for r in validations.itertuples()
                ),
            )
            copy_rows(
                cur,
                "gold.forecasts",
                [
                    "model_run_id",
                    "model",
                    "origin",
                    "store_id",
                    "date",
                    "prediction",
                    "schedule_source",
                ],
                (
                    (
                        mid,
                        r.model,
                        r.origin.date(),
                        int(r.store_id),
                        r.date.date(),
                        float(r.prediction),
                        r.schedule_source,
                    )
                    for r in predictions.itertuples()
                ),
            )
            copy_rows(
                cur,
                "gold.metrics",
                ["model_run_id", "model", "origin", "segment", "mae", "wape", "rmspe", "n"],
                (
                    (
                        mid,
                        m["model"],
                        m["origin"],
                        m["segment"],
                        m["mae"],
                        m["wape"],
                        m["rmspe"],
                        m["n"],
                    )
                    for m in all_metrics
                ),
            )
        stats.update(
            model_run_id=mid,
            training_rows=len(history),
            forecast_rows=len(predictions),
            validation_rows=len(validations),
        )
        (ARTIFACTS / "latest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        return mid
