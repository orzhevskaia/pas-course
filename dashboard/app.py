import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from pas.db import fetch
from pas.config import ARTIFACTS

st.set_page_config(page_title="Rossmann · Forecast", page_icon="◈", layout="wide")
st.markdown(
    """<style>
.stApp {background:#f5f6fa;color:#16243b} .block-container{padding-top:4.5rem;max-width:1500px}
[data-testid="stSidebar"]{background:#14243b} [data-testid="stSidebar"] *{color:#eaf0f9}
[data-testid="stSidebar"] button *{color:#14243b!important}
[data-testid="stMetric"]{background:white;border:1px solid #e3e8f0;border-radius:14px;padding:18px}
h1{letter-spacing:-1px!important} .eyebrow{font-size:12px;letter-spacing:3px;color:#58708f;font-weight:700}
.notice{padding:14px 20px;background:#eaf0fa;border-radius:10px;margin-bottom:20px;color:#30476b}
</style>""",
    unsafe_allow_html=True,
)


@st.cache_data(ttl=20, show_spinner=False)
def query(sql, params=None):
    return pd.DataFrame(fetch(sql, params))


def plot(fig):
    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Arial", "color": "#30435f"},
        margin=dict(l=10, r=10, t=35, b=10),
        legend_title_text="",
    )
    st.plotly_chart(fig, use_container_width=True)


def num(x):
    return f"{x:,.0f}".replace(",", " ")


st.sidebar.markdown("## ◈ ROSSMANN\n**Forecast & Data Control**")
page = st.sidebar.radio(
    "Раздел", ["Прогноз продаж", "Контроль данных", "Происхождение данных", "О проекте"]
)
st.sidebar.divider()
st.sidebar.caption(
    "Учебный прототип · ПАС\n\nИсторический replay · 2013–2015\n\nОбновление панелей каждые 30 секунд"
)


def business():
    st.markdown('<div class="eyebrow">ПРЕДМЕТНЫЙ ДАШБОРД</div>', unsafe_allow_html=True)
    st.title("Выручка под контролем")
    st.caption(
        "Ожидаемая выручка по магазинам и измеренная точность прогноза на горизонте 6 недель."
    )
    latest = query(
        "SELECT model_run_id,origin,min(date) start,max(date) finish FROM gold.latest_forecasts GROUP BY model_run_id,origin"
    )
    if latest.empty:
        st.info(
            "Прогноз ещё не опубликован. Запустите make run; здесь появятся результаты успешного расчёта."
        )
        return
    mid = str(latest.iloc[0].model_run_id)
    origin = latest.iloc[0].origin
    stores = query(
        "SELECT store_id,store_type,assortment,region FROM silver.stores ORDER BY store_id"
    )
    c1, c2, c3 = st.columns([1, 1, 2])
    model = c1.selectbox("Модель", ["HistGradientBoosting", "Seasonal Naive"])
    kind = c2.selectbox("Формат магазина", ["Все"] + sorted(stores.store_type.unique()))
    eligible = stores if kind == "Все" else stores[stores.store_type == kind]
    chosen = c3.multiselect("Магазины · пусто = все", eligible.store_id.tolist(), max_selections=30)
    ids = chosen or eligible.store_id.tolist()
    dates = st.date_input(
        "Период прогноза",
        (latest.iloc[0].start, latest.iloc[0].finish),
        min_value=latest.iloc[0].start,
        max_value=latest.iloc[0].finish,
    )
    if len(dates) != 2:
        st.info("Выберите начало и конец периода.")
        return
    pred = query(
        """SELECT date,sum(prediction) prediction,count(DISTINCT store_id) stores,
      count(*) FILTER(WHERE schedule_source LIKE 'assumed%%') assumed FROM gold.latest_forecasts
      WHERE model=%s AND store_id=ANY(%s) AND date BETWEEN %s AND %s GROUP BY date ORDER BY date""",
        (model, ids, dates[0], dates[1]),
    )
    actual = query(
        """SELECT date,sum(sales) sales FROM silver.sales_daily
      WHERE store_id=ANY(%s) AND date BETWEEN %s::date-84 AND %s GROUP BY date ORDER BY date""",
        (ids, origin, origin),
    )
    validation = query(
        """SELECT model,origin,mae,wape,rmspe,n FROM gold.metrics
      WHERE model_run_id=%s AND segment='all' ORDER BY origin,model""",
        (mid,),
    )
    holdout = validation[
        (validation.model == model) & (validation.origin == validation.origin.max())
    ]
    cols = st.columns(4)
    cols[0].metric("Прогноз на выбранный период", num(pred.prediction.sum()) + " €")
    cols[1].metric("Магазинов в выборке", num(len(ids)))
    cols[2].metric(
        "WAPE · последний holdout", f"{holdout.iloc[0].wape:.1%}" if not holdout.empty else "—"
    )
    cols[3].metric("Дата выпуска", str(origin))
    st.caption("Метрика WAPE относится ко всей сети. Точность выбранных магазинов доступна ниже.")
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(x=actual.date, y=actual.sales, name="Факт", line=dict(color="#233f63", width=2))
    )
    fig.add_trace(
        go.Scatter(
            x=pred.date, y=pred.prediction, name="Прогноз", line=dict(color="#ef6b52", width=3)
        )
    )
    fig.update_layout(title="Факт и ожидаемая дневная выручка, EUR", height=380, yaxis_title="EUR")
    plot(fig)
    st.markdown(
        f'<div class="notice">Расчёт от {origin}. Для {int(pred.assumed.sum()):,} магазинодней выбранного периода расписание оценено по календарю; промо предполагается равным 0. Остальные используют плановый сценарий Kaggle test.</div>',
        unsafe_allow_html=True,
    )
    left, right = st.columns([1.5, 1])
    by_store = query(
        """SELECT f.store_id,st.store_type,sum(f.prediction) revenue FROM gold.latest_forecasts f
      JOIN silver.stores st USING(store_id) WHERE f.model=%s AND f.store_id=ANY(%s) AND f.date BETWEEN %s AND %s
      GROUP BY f.store_id,st.store_type ORDER BY revenue DESC""",
        (model, ids, dates[0], dates[1]),
    )
    with left:
        st.subheader("Магазины с наибольшей ожидаемой выручкой")
        top = by_store.head(12).copy()
        top["Магазин"] = top.store_id.astype(str)
        plot(
            px.bar(
                top, x="revenue", y="Магазин", orientation="h", color_discrete_sequence=["#2c668c"]
            ).update_layout(yaxis={"autorange": "reversed"}, xaxis_title="EUR", height=380)
        )
    with right:
        st.subheader("План по магазинам")
        st.dataframe(
            by_store.rename(
                columns={"store_id": "Магазин", "store_type": "Формат", "revenue": "Прогноз, EUR"}
            ),
            hide_index=True,
            use_container_width=True,
            height=360,
        )
    st.subheader("Проверка на данных с известным фактом")
    st.caption(
        "Три последовательных окна по 42 дня. Последнее окно — финальный holdout; параметры зафиксированы заранее. Open/Promo/праздники считаются известным планом."
    )
    plot(
        px.bar(
            validation,
            x="origin",
            y="wape",
            color="model",
            barmode="group",
            color_discrete_sequence=["#ef6b52", "#2c668c"],
        ).update_layout(
            yaxis_tickformat=".0%", yaxis_title="WAPE", xaxis_title="Дата выпуска", height=280
        )
    )
    st.dataframe(
        validation.rename(
            columns={
                "origin": "Дата выпуска",
                "mae": "MAE, EUR",
                "wape": "WAPE",
                "rmspe": "RMSPE",
                "n": "Строк",
            }
        ),
        hide_index=True,
        use_container_width=True,
    )
    fold = st.selectbox(
        "Окно для проверки факта и прогноза",
        sorted(validation.origin.unique()),
        index=len(validation.origin.unique()) - 1,
    )
    backtest = query(
        """SELECT date,sum(actual) actual,sum(prediction) prediction FROM gold.validation_predictions
      WHERE model_run_id=%s AND model=%s AND origin=%s AND store_id=ANY(%s) GROUP BY date ORDER BY date""",
        (mid, model, fold, ids),
    )
    if not backtest.empty:
        plot(
            px.line(
                backtest,
                x="date",
                y=["actual", "prediction"],
                color_discrete_sequence=["#233f63", "#ef6b52"],
            ).update_layout(
                title="Backtest выбранных магазинов",
                yaxis_title="EUR",
                xaxis_title="Дата",
                height=300,
            )
        )
    store_metrics = query(
        """SELECT segment,mae,wape,rmspe FROM gold.metrics WHERE model_run_id=%s AND model=%s
      AND segment LIKE 'store_%%' AND segment NOT LIKE 'store_type_%%' ORDER BY wape DESC NULLS LAST""",
        (mid, model),
    )
    if not store_metrics.empty:
        store_metrics["store_id"] = store_metrics.segment.str.replace("store_", "").astype(int)
        st.caption(
            "Магазины с высокой относительной ошибкой: кандидаты для ручной проверки плана. Метрики по трём окнам."
        )
        st.dataframe(
            store_metrics[store_metrics.store_id.isin(ids)][
                ["store_id", "mae", "wape", "rmspe"]
            ].head(20),
            hide_index=True,
            use_container_width=True,
        )
    export = query(
        """SELECT store_id,date,prediction,schedule_source FROM gold.latest_forecasts
      WHERE model=%s AND store_id=ANY(%s) AND date BETWEEN %s AND %s ORDER BY store_id,date""",
        (model, ids, dates[0], dates[1]),
    )
    st.download_button(
        "Скачать выбранный прогноз CSV",
        export.to_csv(index=False).encode("utf-8-sig"),
        "rossmann_forecast.csv",
        "text/csv",
    )
    with st.expander("Определения и ограничения"):
        st.markdown(
            "**Показатель:** дневная выручка, EUR. Это прогноз продаж магазина; SKU, запасы и заказы поставщикам в данных отсутствуют.\n\n**WAPE** = сумма абсолютных ошибок / сумма факта. **MAE** — средняя абсолютная ошибка в EUR. **RMSPE** считается только на строках с фактом > 0.\n\nГеография магазинов синтетическая. Погода не входит в модель. Будущее число покупателей не используется. Прогноз является условным сценарием при заданных Open/Promo, а не гарантией выручки. Интервалы неопределённости в этой версии не рассчитываются."
        )


def operations():
    st.markdown('<div class="eyebrow">ОПЕРАЦИОННЫЙ ДАШБОРД</div>', unsafe_allow_html=True)
    st.title("Можно ли доверять данным?")
    status = query(
        """SELECT max(date) max_date,count(*) rows,count(DISTINCT store_id) stores FROM silver.sales_daily"""
    )
    runs = query(
        "SELECT stage,status,logical_date,started_at,ended_at,stats,error FROM meta.runs ORDER BY started_at DESC LIMIT 80"
    )
    if runs.empty:
        st.info("Пока нет запусков. Выполните make run.")
        return
    quality = query("""SELECT rule,kind,severity,failed_count,checked_count,passed,created_at,details FROM meta.dq_results
      WHERE run_id=(SELECT run_id FROM meta.runs WHERE stage='quality_gate' ORDER BY started_at DESC LIMIT 1) ORDER BY id""")
    cols = st.columns(4)
    cols[0].metric("Строк продаж", num(status.iloc[0]["rows"]))
    cols[1].metric("Последний день данных", str(status.iloc[0].max_date))
    cols[2].metric(
        "Критических DQ-нарушений",
        num(quality.loc[(quality.severity == "error") & ~quality.passed, "failed_count"].sum())
        if not quality.empty
        else "—",
    )
    quarantine = query("SELECT count(*) n FROM meta.quarantine")
    cols[3].metric("Строк в карантине", num(quarantine.iloc[0].n))
    latest_pipeline = runs[runs.stage == "pipeline"]
    if not latest_pipeline.empty:
        p = latest_pipeline.iloc[0]
        {"success": st.success, "failed": st.error, "running": st.info}[p.status](
            f"Последний pipeline: {p.status} · логическая дата {p.logical_date} · запуск {p.started_at}"
        )
    st.caption(
        "Актуальность replay оценивается относительно логической даты запуска; реальные даты запуска показаны отдельно. Ошибки demonstration_failure — намеренный сценарий проверки журнала."
    )
    quality_column, volume_column = st.columns([1.3, 1])
    with quality_column:
        st.subheader("Результаты контроля качества")
        if quality.empty:
            st.info("Контроль качества ещё не запускался.")
        else:
            st.dataframe(
                quality.drop(columns=["details"]), hide_index=True, use_container_width=True
            )
    with volume_column:
        st.subheader("Объёмы загруженных партий")
        batches = query(
            "SELECT source,sum(rows_count) rows,count(*) batches FROM meta.batches GROUP BY source"
        )
        if not batches.empty:
            plot(
                px.bar(batches, x="source", y="rows", color="source").update_layout(
                    showlegend=False, height=300
                )
            )
    st.subheader("Журнал запусков")
    runs["duration_sec"] = (
        (pd.to_datetime(runs.ended_at, utc=True) - pd.to_datetime(runs.started_at, utc=True))
        .dt.total_seconds()
        .round(2)
    )
    stage = st.multiselect("Этапы", sorted(runs.stage.unique()))
    st.dataframe(
        runs[runs.stage.isin(stage)] if stage else runs, hide_index=True, use_container_width=True
    )
    st.subheader("Карантин: причина и исходная запись")
    rejects = query(
        "SELECT source,reason,payload,created_at FROM meta.quarantine ORDER BY id DESC LIMIT 100"
    )
    st.dataframe(
        rejects, hide_index=True, use_container_width=True
    ) if not rejects.empty else st.success("В текущих источниках отклонённых записей нет.")
    st.subheader("Погода · внешний источник")
    weather = query(
        "SELECT region,count(*) days,min(date) first,max(date) last,avg(temperature) temperature FROM silver.weather_daily GROUP BY region ORDER BY region"
    )
    st.dataframe(weather, hide_index=True, use_container_width=True)
    st.caption(
        "Реальные ответы Open-Meteo для фиксированных синтетических регионов. Привязка не является адресами магазинов; погодные данные используются для демонстрации интеграции."
    )


def lineage():
    st.markdown('<div class="eyebrow">МЕТАДАННЫЕ И ИСТОРИЯ</div>', unsafe_allow_html=True)
    st.title("От показателя до источника")
    st.code(
        "Прогноз выручки → gold.latest_forecasts → model_run_id\n→ meta.model_runs → data_hash + code_hash + config + artifact\n→ silver.sales_daily → batch_id → meta.batches\n→ bronze.sales → data/train.csv",
        language="text",
    )
    st.caption(
        "ML использует продажи. Погодная ветка проходит через gold.sales_enriched и доступна в аналитическом контексте."
    )
    catalog = query("SELECT * FROM meta.assets ORDER BY kind,name")
    if catalog.empty:
        st.info("Каталог ещё не инициализирован.")
        return
    asset = st.selectbox(
        "Показатель, таблица или процесс",
        catalog.name.tolist(),
        index=catalog.name.tolist().index("kpi.forecast_revenue"),
    )
    entry = catalog[catalog.name == asset].iloc[0]
    st.write(entry.description)
    st.json(entry.definition)
    chain = query(
        """WITH RECURSIVE ancestry(upstream,downstream,depth,path) AS (
      SELECT upstream,downstream,1,ARRAY[downstream,upstream] FROM meta.lineage WHERE downstream=%s
      UNION ALL SELECT l.upstream,l.downstream,a.depth+1,a.path||l.upstream FROM meta.lineage l
      JOIN ancestry a ON l.downstream=a.upstream WHERE NOT l.upstream=ANY(a.path))
      SELECT DISTINCT upstream,downstream,depth FROM ancestry ORDER BY depth""",
        (asset,),
    )
    st.dataframe(chain, hide_index=True, use_container_width=True)
    st.subheader("Версии моделей и снимки данных")
    model_runs = query("SELECT * FROM meta.model_runs ORDER BY created_at DESC LIMIT 10")
    st.dataframe(model_runs, hide_index=True, use_container_width=True)
    source_batches = query("""SELECT mb.model_run_id,b.batch_id,b.source,b.partition_key,b.checksum FROM meta.model_batches mb
      JOIN meta.batches b USING(batch_id) WHERE mb.model_run_id=(SELECT model_run_id FROM meta.model_runs ORDER BY created_at DESC LIMIT 1)
      ORDER BY b.partition_key""")
    st.caption(
        "Зафиксированные исходные партии модели; снимок training_snapshot.parquet и код source_code.zip сохраняются рядом с артефактом."
    )
    st.dataframe(source_batches, hide_index=True, use_container_width=True)
    st.subheader("SCD2 · атрибуты магазина во времени")
    sid = st.number_input("Магазин для историзации", 1, 1115, 1)
    versions = query(
        "SELECT version_id,valid_from,valid_to,attributes,reason,recorded_at,run_id FROM silver.store_versions WHERE store_id=%s ORDER BY valid_from",
        (int(sid),),
    )
    st.dataframe(versions, hide_index=True, use_container_width=True)
    st.caption(
        "Полуоткрытые интервалы [valid_from, valid_to). Начальный снимок условно распространён на историю с 2013-01-01. Демонстрационное изменение после 2015-07-31 явно помечено synthetic demo."
    )
    st.subheader("Проследить фактическую продажу")
    day = st.date_input("Дата факта", value=__import__("datetime").date(2015, 7, 31))
    trace = query(
        """SELECT s.store_id,s.date,s.sales,s.batch_id,b.source,b.partition_key,b.checksum,b.run_id,
      r.started_at,br.row_hash,br.batch_id original_raw_batch,br.payload FROM silver.sales_daily s JOIN meta.batches b USING(batch_id)
      JOIN meta.runs r ON r.run_id=b.run_id JOIN bronze.sales br ON br.row_hash=s.source_row_hash
      WHERE s.store_id=%s AND s.date=%s ORDER BY br.row_hash LIMIT 10""",
        (int(sid), day),
    )
    st.dataframe(trace, hide_index=True, use_container_width=True)
    st.caption("Каталог и история обновлены.")


def about():
    st.title("Rossmann Forecast")
    st.markdown("""Прогнозно-аналитическая система для профиля «Управление данными».

**Задача:** прогноз дневной выручки магазинов на 42 дня и контроль жизненного цикла данных.

**Источники:** Rossmann CSV и Open-Meteo JSON API. Исторические продажи поступают месячными порциями. Потоковая шина не требуется для выбранного сценария.

**Хранение:** PostgreSQL, Bronze → Silver → Gold и Meta. Проверки качества, карантин, журналирование, SCD2 и каталог lineage.

**Модели:** Seasonal Naive и HistGradientBoosting. Временная валидация без перемешивания; лаги не менее 42 дней. Продажи нулевых дней сохраняются.

**Ограничения:** данные 2013–2015 годов; replay не является актуальной работой сети. География синтетическая. Прогноз зависит от предположений о графике открытия и промо. Начальная история атрибутов магазина неизвестна.
""")
    results = ARTIFACTS / "RESULTS.md"
    if results.exists():
        st.markdown(results.read_text())
    latest = ARTIFACTS / "latest.json"
    if latest.exists():
        summary = json.loads(latest.read_text())
        importance = pd.DataFrame(summary.get("importance", []))
        if not importance.empty:
            st.subheader("Чувствительность к признакам")
            st.caption(
                "Permutation importance на выборке финального holdout, ошибка log1p(Sales); интерпретационный анализ после оценки, не подбор параметров."
            )
            plot(
                px.bar(
                    importance.head(12), x="importance", y="feature", orientation="h"
                ).update_layout(yaxis={"autorange": "reversed"})
            )


@st.fragment(run_every=30)
def render():
    try:
        {
            "Прогноз продаж": business,
            "Контроль данных": operations,
            "Происхождение данных": lineage,
            "О проекте": about,
        }[page]()
    except Exception as exc:
        st.error("Данные пока недоступны. Проверьте состояние контейнера db и выполните make run.")
        with st.expander("Диагностика"):
            st.code(str(exc))


render()
