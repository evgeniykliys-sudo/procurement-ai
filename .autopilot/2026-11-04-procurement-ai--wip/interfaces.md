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

### Таск 01 (фундамент) — фактические интерфейсы
- `Config`: все поля из списка выше + `season_own_weight=0.5`, `lot_rounding=((100,10),(1000,50),(inf,100))`, `reliability_receipt_window_days=180`; `reliability_max_uplift=1.2`.
- `Dataset(sku, sales, stock, po, receipts, suppliers)`; `DataError`; месяцы — `Period[M]`, даты — `datetime.date`. `po_id` бывает `None` (23 строки).
- `cut_dataset`: у заказов в пути ещё и `qty_received := 0`; убирает карточки, заведённые после даты без продаж; пересчитывает в `sku` first/last_sale_month, months_with_sales, qty_total, avg_markup_pct_12m, on_order_qty.
- `DemandResult.per_sku`: `sku_id, category, base_monthly, trend, sigma_monthly, history_months, censored_months, sales_12m, orders_12m, returns_12m, rank_in_category, flags`. **Колонку флагов брать только `df["flags"]`** — `df.flags` это атрибут pandas. Флаги: «мало истории», «нет продаж {dead_months} мес», «весь год без остатка».
- `forecast[sku_id, period, qty(float)]`; `stock_on -> Series[int]`.
- `in_transit -> (arrivals[sku_id, po_id, supplier_id, eta, qty, late, promised_date], stale[po_id, sku_id, supplier_id, promised_date, qty_pending, days_overdue])`.
- `supplier_reliability -> [supplier_id, fill_ratio, orders_n, uplift, qty_ordered, qty_received]` (qty_received — по receipts.csv, D01).
- `lot_size(ds, cfg=None) -> Series[int]`.
- На 2026-08-13: в пути 22 строки / 17 127 шт., опаздывают 5, stale 4; мёртвых по продажам 331.
- **Таску 02:** SKU с `supply_type = unknown` → плечо помечать «оценочное» независимо от `lead_source` (R34i).
- Ремонт таска 01: в `Config` ещё `season_outlier_factor=2.0`, `level_min_season_index=0.5`, `forecast_cap_vs_last_year=2.0`. Новые флаги `per_sku["flags"]`: «уровень по 24 мес», «уровень без сезонной поправки», «упущенный спрос — проверить» (D03: прогноз на 12 мес ≤ 2× sales_12m при истории ≥ 12; у 71 SKU на 2026-08-13). Профиль категории — по суммарному ряду категории с обрезкой выбросов; уровень новинки — медиана.
- Бэктест-ориентир (cut 2025-08-01, WAPE модель/наивный): space_heaters 0.56/0.98, string_trimmers 0.36/0.93, trimmer_blades 0.38/0.96, concrete_mixers 0.47/0.48, sanitary_faucets 0.35/0.26, grain_mills 1.18/0.65.

### Таск 02 (решение) — фактические интерфейсы
- `build_plan(ds, demand, cfg, as_of) -> Plan`; в `Plan` ещё `arrivals`, `series{sku_id: {history, forecast, stock_proj}}`, `config`.
- `items` — колонки из списка выше + `supply_type, weight_kg, sales_12m, orders_12m, returns_12m, rank_in_category, trend, base_monthly, sigma_monthly (устойчивая σ, D04), history_months, forecast_12m`. У `order_later` `order_qty` — оценка на дату `order_by` (`explain.at`).
- **`explain` (фактическая схема, D06):** `forecast_horizon` (прогноз L..L+R), `safety`, `stock_at_arrival` = max(0, `stock` + `transit_lead` − `forecast_lead`), `transit` (приходы в (L; L+R]), `gross = forecast_horizon + safety − stock_at_arrival − transit`, `uplift`, `lot`, `lot_source` («партия»|«шаг»), `final` (= order_qty); справочно `stock, transit_lead, forecast_lead, arrival, at, horizon_days, z, sigma_monthly, excluded, would_order, days_short, excess_qty`. «Почему столько» на дашборде раскладывается именно так.
- `plan_to_json(plan)` → `{as_of, generated_at, kpi, items, supplier_orders, calendar, stale_po, reliability, arrivals, config, series}`. Ключи `kpi`: `order_now_positions, order_now_qty, order_now_suppliers, order_now_no_supplier, deficit_unavoidable, deficit_if_late, excess, review_price, low_markup, no_cost, running_out, dead, dead_sell, dead_close, in_transit_future_qty` и др.
- Флаги плана: «нет себестоимости», «низкая наценка», «скоро кончится», «плечо оценочное», «партия больше годового спроса».
- `run_backtest(...)` → `{by_category, total, series, actual, model, naive, cut, months}`. `history_index(dir) -> [{as_of, file, kpi}]`; `diff_runs` → + `as_of, prev_as_of`.
- CLI: `run [--raw] [--as-of] [--no-ai] [--notify] [--config] [--history] [--out] [--site]` пишет `out/plan.json, out/backtest.json, out/diff.json, out/note.json`, `history/<as_of>.json`, `site/`; `compute_plan(raw, cfg, as_of)`; `--as-of` раньше данных → `cut_dataset`. `seed-history`.
- Ремонт таска 02: `Config` + `stockout_min_lost_qty=1`, `min_order_qty=1`, `safety_cap_months=2`. `items.cover_days`, `rank_in_category` — int|None. `explain` + `lost_lead` (потеряно до as_of+L), `cap`; `lot_source` ещё «потолок». `stockout_date`/`cover_days` — первый день, когда потеряно ≥ 1 шт.; `cover_days = None` — не кончается за горизонт проекции («хватит > 2 лет»).
- `supplier_orders[supplier_id ("—" если не заведён), supplier_label, sku_id, order_qty, weight_kg, order_by, expected_arrival]` — на сайте показывать `supplier_label`.
- `calendar` — с повторными заказами по каждому SKU на 6 мес: `[month, positions, qty]`.
- `history/<as_of>.json` = `{as_of, kpi, items:[{sku_id, status, order_qty, risk, category, supplier_id}]}` (~119 КБ, побайтно стабилен).
- KPI 2026-08-13: к заказу сейчас 189 поз. / 43 066 шт. / 18 поставщиков (+2 без поставщика); дефицит неизбежен 205, если опоздать 16; излишки 84; позже 90; на пересмотр 115 (68+47); мёртвых 331.
