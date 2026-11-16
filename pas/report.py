import json
import pandas as pd
import plotly.express as px
from .config import ARTIFACTS
from .db import fetch


def generate(as_of):
    ARTIFACTS.mkdir(exist_ok=True)
    counts = fetch("""SELECT (SELECT count(*) FROM silver.sales_daily) sales,
     (SELECT count(*) FROM silver.stores) stores,(SELECT count(*) FROM silver.weather_daily) weather,
     (SELECT count(*) FROM meta.quarantine) quarantine,(SELECT count(*) FROM silver.store_versions) scd_versions,
     (SELECT count(*) FROM meta.assets) catalog_assets,(SELECT count(*) FROM meta.lineage) lineage_edges""")[
        0
    ]
    daily = pd.DataFrame(
        fetch(
            "SELECT date,sum(sales) revenue,sum(customers) customers FROM silver.sales_daily GROUP BY date ORDER BY date"
        )
    )
    px.line(daily, x="date", y="revenue", title="Rossmann: дневная выручка сети, EUR").write_html(
        ARTIFACTS / "eda_revenue.html", include_plotlyjs=True
    )
    weekday = pd.DataFrame(
        fetch(
            "SELECT day_of_week,avg(sales) mean_sales FROM silver.sales_daily GROUP BY day_of_week ORDER BY day_of_week"
        )
    )
    px.bar(
        weekday, x="day_of_week", y="mean_sales", title="Средняя выручка магазина по дням недели"
    ).write_html(ARTIFACTS / "eda_weekday.html", include_plotlyjs=True)
    models = fetch("""SELECT m.model_run_id,m.data_hash,m.code_hash,m.origin,m.artifact_path FROM meta.model_runs m
     JOIN meta.runs r USING(run_id) WHERE r.status='success' ORDER BY m.created_at DESC LIMIT 1""")
    metrics = []
    if models:
        metrics = fetch(
            "SELECT model,origin,mae,wape,rmspe,n FROM gold.metrics WHERE model_run_id=%s AND segment='all' ORDER BY origin,model",
            (str(models[0]["model_run_id"]),),
        )
    results = {"logical_date": as_of, "counts": counts, "model": models, "validation": metrics}
    (ARTIFACTS / "results.json").write_text(
        json.dumps(results, default=str, ensure_ascii=False, indent=2)
    )
    text = [
        "# Фактические результаты запуска",
        f"Логическая дата: {as_of}.",
        "",
        "| Объект | Число |",
        "|---|---:|",
    ]
    text.extend(f"| {k} | {v:,} |" for k, v in counts.items())
    text.extend(
        [
            "",
            "## Временная валидация",
            "",
            "| Дата выпуска | Модель | MAE, EUR | WAPE | RMSPE | Строк |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    text.extend(
        f"| {m['origin']} | {m['model']} | {m['mae']:.2f} | {m['wape']:.2%} | {m['rmspe']:.2%} | {m['n']} |"
        for m in metrics
    )
    text.extend(
        [
            "",
            "WAPE учитывает нулевые продажи в числителе; RMSPE рассчитывается только при actual > 0.",
            "Последнее окно — финальный holdout; параметры модели зафиксированы заранее. Погодные данные и Customers не входят в признаки.",
            "География синтетическая, региональные результаты не описывают реальные регионы сети.",
        ]
    )
    (ARTIFACTS / "RESULTS.md").write_text("\n".join(text))
