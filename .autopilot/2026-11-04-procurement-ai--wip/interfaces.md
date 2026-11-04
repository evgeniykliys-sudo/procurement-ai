# Интерфейсы и правила проекта

Первый файл, который читает каждый исполнитель.

## Правила проекта

- Python 3.12, venv `.venv/` (`.venv/Scripts/python.exe` на Windows). Зависимости — `requirements.txt` (pandas, numpy, openpyxl, jinja2, requests, pytest). Новые зависимости — только с причиной в коммите.
- Весь текст для пользователя (дашборд, ошибки, дайджест, записка) — **по-русски**. Имена в коде — английские.
- **Числа считает только Python.** LLM получает готовые числа и пишет текст; LLM-ответ никогда не меняет количества.
- Все бизнес-параметры — в `config.toml`, читаются через `procurement.config.load_config()`. Никаких магических чисел в коде модели.
- Секреты — только из переменных окружения (`DEEPSEEK_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`), локально — `.env` (в `.gitignore`), шаблон — `.env.example`. Никогда не печатать значение.
- Данные: `data/raw/*.csv` — вход (не изменять). Выход расчёта — `out/` (в `.gitignore`), сайт — `site/` (собирается, в `.gitignore`), история — `history/<as_of>.json` (коммитится).
- Тесты — `pytest` в `tests/`, синтетические мини-наборы данных в фикстурах; один интеграционный тест на реальном `data/raw`.
- Коммиты без строки Co-Authored-By.
- Даты — `datetime.date`; месяц — строка `YYYY-MM` только на входе, внутри — `pd.Period('M')`.

## Команды

| Команда | Что делает |
|---|---|
| `.venv/Scripts/python.exe -m pip install -r requirements.txt` | зависимости (venv уже создан, pandas/numpy/openpyxl/jinja2/requests/pytest/edge-tts/imageio-ffmpeg стоят) |
| `.venv/Scripts/python.exe -m procurement run --no-ai` | полный прогон без LLM |
| `.venv/Scripts/python.exe -m pytest -q` | **единая проверка** (тесты; `tests/test_smoke.py` импортирует все модули) |
| `.venv/Scripts/python.exe -m pytest -q tests/test_demand.py` | один файл |

Не трогать: `data/raw/` (вход), `.autopilot/` (ведёт оркестратор), `AGENTS.md` между маркерами autopilot. Оболочка — Git Bash на Windows; пути с `/`.

## Заглушки между волнами

Таск 02 создаёт `procurement/site.py`, `procurement/ai.py`, `procurement/notify.py` как **заглушки с финальными сигнатурами** (site пишет минимальный `site/index.html` с KPI; ai возвращает `{"ok": False, "markdown": "", "sku_notes": {}, "error": "ИИ-слой не подключён", "model": ""}`; notify возвращает `False`) и вызывает их из CLI. Таски 03 и 04 заменяют тела, сигнатуры не меняют.

## Границы, решённые в спецификации

### `procurement/config.py`
- `load_config(path="config.toml") -> Config` — dataclass, поля (имена фиксированы):
  `min_markup_pct=25`, `default_cover_days=60`, `review_period_days=30`, `service_level={"A":0.95,"B":0.90,"C":0.80}`, `abc_thresholds=(0.80,0.95)`,
  `lead_default_days=123`, `lead_min_days=30`, `lead_max_days=180`, `excess_cover_months=12`, `overdue_grace_days=90`, `overdue_eta_days=30`,
  `reliability_min_ratio=0.95`, `reliability_max_uplift=1.5`, `reliability_min_age_days=180`, `trend_clip=(0.5,1.5)`,
  `new_sku_months=6`, `partial_day=13`, `shrink_min_months=24`, `forecast_months=18`, `dead_months=12`, `change_threshold_pct=20`, `as_of: date|None=None`,
  `llm_model="deepseek-chat"`, `llm_base_url="https://api.deepseek.com"`, `llm_timeout_s=60`, `site_url=""`.

### `procurement/data.py` — владеет чтением и проверкой CSV
- `load_dataset(raw_dir) -> Dataset` — dataclass с DataFrame-полями `sku, sales, stock, po, receipts, suppliers`; валидирует наличие файлов/колонок/дат, при ошибке — `DataError(сообщение по-русски)`.
- `detect_as_of(ds, cfg) -> date` — `cfg.as_of`, если задан; иначе: если последний месяц продаж неполный (`partial_month=1`) — дата = этот месяц, день `cfg.partial_day` (по умолчанию 13, параметр в `config.toml` с комментарием «день выгрузки неполного месяца»); если полный — первый день следующего месяца.
- `cut_dataset(ds, as_of) -> Dataset` — обрезка на дату для «прошлого» прогона и бэктеста (продажи месяцев < as_of-месяца, остатки ≤, приходы <, заказы с `order_date < as_of`; «в пути» на ту дату — приближение: если `promised_date ≥ as_of`, то `qty_pending := qty_pending + qty_received` и `status := open`; иначе поля как есть). Последний месяц продаж в обрезке — полный (`partial_month = 0`).

### `procurement/demand.py` — владеет прогнозом
- `build_demand(ds, cfg, as_of) -> DemandResult`:
  - `profiles: DataFrame[category, month(1..12), index]` (среднее = 1),
  - `per_sku: DataFrame[sku_id, base_monthly, trend, sigma_monthly, history_months, censored_months, flags(list[str])]`,
  - `forecast: DataFrame[sku_id, period(Period M), qty]` на `cfg.forecast_months` вперёд от месяца as_of (первый месяц — только оставшаяся доля дней).
- `stock_on(ds, as_of) -> Series[sku_id -> qty]` — остаток на дату анализа.

### `procurement/supply.py` — владеет плечом, «в пути», надёжностью
- `lead_times(ds, cfg) -> DataFrame[sku_id, lead_days, lead_source("sku"|"supplier"|"default")]`
- `in_transit(ds, cfg, as_of) -> (arrivals: DataFrame[sku_id, po_id, eta(date), qty, late(bool)], stale: DataFrame[po_id, sku_id, promised_date, qty_pending, days_overdue])`
- `supplier_reliability(ds, cfg, as_of) -> DataFrame[supplier_id, fill_ratio, orders_n, uplift]`
- `lot_size(ds) -> Series[sku_id -> int]` — кратность партии.

### `procurement/plan.py` — владеет решением
- `build_plan(ds, demand, cfg, as_of) -> Plan` — `Plan.items: DataFrame`, по строке на каждый SKU из `sku_dim`, колонки (фиксированы, их читают сайт, LLM-слой, дайджест, скилл):
  `sku_id, category, supplier_id, abc, status, order_qty, order_by(date|None), stockout_date(date|None), cover_days(int|None), stock_now, in_transit_qty, next_eta, lead_days, lead_source, cover_target_days, safety_stock, demand_horizon, reliability_uplift, markup_12m, risk(list[str]), flags(list[str]), explain(dict)`
  - `status` ∈ `order_now | order_later | ok | review_price | dead_sell | dead_close | no_demand`
  - `risk` ⊂ `deficit_unavoidable | deficit_if_late | excess | late_po | low_reliability`
  - `explain` — числа расшифровки «почему столько»: `{forecast_horizon, safety, stock, transit, gross, uplift, lot, final}`.
- `Plan.supplier_orders: DataFrame[supplier_id, sku_id, order_qty, weight_kg(None), order_by, expected_arrival]`
- `Plan.calendar: DataFrame[month, positions, qty]` — что заказывать по месяцам (6 мес).
- `Plan.stale_po`, `Plan.reliability`, `Plan.as_of`, `Plan.kpi: dict`.
- `plan_to_json(plan) -> dict` — сериализация (даты ISO).

### `procurement/backtest.py`
- `run_backtest(ds, cfg, cut=date(2025,8,1), months=12) -> dict` — `{by_category:[{category, wape_model, wape_naive, bias_model, bias_naive}], total:{…}, series:{category:[{month, actual, model, naive}]}}`.

### `procurement/history.py`
- `save_run(plan_json, history_dir) -> Path` — `history/<as_of>.json` (перезапись при той же дате).
- `previous_run(history_dir, as_of) -> dict|None` — последний прогон с датой < as_of.
- `diff_runs(cur, prev, cfg) -> dict` — `{new_orders, dropped_orders, changed:[{sku_id, prev_qty, qty, pct}], new_risks}`.

### `procurement/ai.py` — единственный модуль, говорящий с LLM
- `analyst_note(summary: dict, cfg, cache_dir) -> {"ok":bool, "markdown":str, "sku_notes":{sku_id:str}, "error":str|None, "model":str}` — промпт из `prompts/analyst.md`; JSON-ответ; SKU вне входа вычищаются; кеш по sha256 входа.
- `build_summary(plan_json, diff, backtest) -> dict` — компактный вход для LLM (≤ ~8 тыс. токенов).

### `procurement/site.py`
- `build_site(plan_json, diff, backtest, note, history_index, out_dir="site")` — `index.html` + `data/*.json` + `exports/*.xlsx|csv` + `assets/` (ECharts в репо `procurement/static/`).

### `procurement/notify.py`
- `send_digest(plan_json, diff, site_url) -> bool` — Telegram Bot API `sendMessage` HTML; без переменных окружения — `False` и строка в лог.

### `procurement/__main__.py` — CLI
- `python -m procurement run [--raw data/raw] [--as-of YYYY-MM-DD] [--no-ai] [--notify]` — полный конвейер: load → as_of → demand → plan → backtest → history → diff → ai → site → (notify).
- `python -m procurement seed-history` — прогон на `cut_dataset(…, 2026-07-01)` и запись в `history/`.

## Швы тестов
- `build_demand`, `build_plan` на синтетическом `Dataset` (фикстура `tests/conftest.py::make_dataset`).
- `analyst_note` с подменённым HTTP (`monkeypatch` на `requests.post`).
- Интеграционный: `run --no-ai` на `data/raw` → `site/index.html` есть, KPI согласованы (68 на пересмотр, 378 мёртвых, 17 127 шт. в пути с датой в будущем).

## Что построили законченные таски

(дописывается по мере работы)
