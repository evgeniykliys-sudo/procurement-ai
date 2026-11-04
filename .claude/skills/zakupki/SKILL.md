---
name: zakupki
description: План закупок импортного товара — пересчитать по свежим CSV, ответить закупщику на вопрос по SKU или поставщику («почему по SKU-X столько?», «что заказать у SUP-Y?»), собрать заказ поставщику, изменить параметр в config.toml и показать, как поменялись KPI. Срабатывает на /zakupki, «пересчитай закупки», «почему столько», «заказ поставщику», «что будет, если поменять покрытие/наценку/уровень сервиса».
---

# /zakupki — помощник закупщика

Проект считает план закупок детерминированным Python: прогноз спроса → решение по каждому SKU → заказы поставщикам. Ты не считаешь сам — ты запускаешь расчёт и читаешь его результат.

## Правила

- **Числа не придумывай и не пересчитывай.** Каждое число в ответе — из `out/*.json` (или из вывода команды). Нет числа в файлах — так и скажи.
- «Почему столько» объясняй только по полю `explain` строки SKU, ссылаясь на его поля.
- Количества заказа не «правь на глаз». Хочется другое число — меняется параметр в `config.toml` и делается пересчёт.
- `data/raw/` не изменяй. Секреты (`.env`) не открывай и не печатай.
- Отвечай по-русски, коротко: сначала ответ, потом расшифровка.

## 0. Python окружения

Команды ниже — для bash (Linux/macOS; Windows — Git Bash). Python проекта: `.venv/bin/python` на Linux/macOS, `.venv/Scripts/python.exe` на Windows. Начинай каждую команду с выбора пути — переменная между вызовами shell не сохраняется:

```bash
PY=.venv/bin/python; [ -x "$PY" ] || PY=.venv/Scripts/python.exe
```

## 1. Пересчитать

```bash
$PY -m procurement run          # с запиской ИИ, если в .env есть DEEPSEEK_API_KEY
$PY -m procurement run --no-ai  # без ИИ, быстрее
```

Итог в двух строках печатается в консоль. Пишет `out/plan.json`, `out/diff.json`, `out/backtest.json`, `out/note.json`, `history/<дата>.json`, сайт `site/`. Дата анализа определяется по данным; задать вручную — `--as-of ГГГГ-ММ-ДД`.

## 2. Вопрос по SKU или поставщику

Читай `out/plan.json` точечно (файл большой — не выводи целиком), например:

```bash
$PY -c "import json,sys; sys.stdout.reconfigure(encoding='utf-8'); p=json.load(open('out/plan.json',encoding='utf-8')); print(json.dumps([r for r in p['items'] if r['sku_id']=='SKU-026A51'], ensure_ascii=False, indent=1))"
```

Что в строке `items[]`:
- `status`: `order_now` — заказать сейчас, `order_later` — позже (дата `order_by`, количество — оценка на ту дату), `ok` — не нужно, `review_price` — на пересмотр цены (низкая наценка `markup_12m` или нет себестоимости), `dead_sell` / `dead_close` — мёртвый ассортимент (распродать / вывести), `no_demand` — нет спроса.
- `risk`: `deficit_unavoidable` — дефицит будет даже при заказе сегодня, `deficit_if_late` — будет, если опоздать, `excess` — излишек, `late_po` — заказ в пути опаздывает, `low_reliability` — поставщик недовозит.
- `stockout_date`, `cover_days` — когда кончится и на сколько дней хватит (`None` — хватит больше чем на горизонт проекции); `stock_now`, `in_transit_qty`, `next_eta`; `lead_days` и `lead_source` (`sku` / `supplier` / `default`) — плечо поставки; `flags` — пометки («мало истории», «плечо оценочное», «упущенный спрос — проверить» и др.).

Как читать `explain` («почему столько»):
1. `forecast_horizon` — прогноз спроса на период, который покрывает заказ (от прихода через `lead_days` ещё на `cover_target_days`).
2. `+ safety` — страховой запас: √(`safety_cover`² + `safety_lead`²) — части окна покрытия и окна плеча, каждая по сезону своего окна (уровень сервиса по ABC, `z`, `sigma_monthly`), не больше `safety_cap` (2 мес. спроса периода покрытия).
3. `− stock_at_arrival` — что физически останется к приходу: `stock` + `transit_lead` (придёт до заказа) − `forecast_lead` (спрос до прихода) + `lost_to_arrival` (спрос, потерянный, пока товара не было: партия в пути на него не уходит, D12), не меньше нуля.
4. `− transit` — что ещё придёт по открытым заказам внутри покрываемого периода.
5. `= gross`, затем `× uplift` (поправка на недовоз поставщика), округление до партии `lot` (`lot_source`: «партия», «шаг», «потолок»; `typical_lot` — медиана приходов, до неё округляем, только если это добавляет ≤ 1 мес. спроса) → `final`, он же `order_qty`. `merged_until_review` — следующий заказ, который понадобился бы раньше пересмотра и вошёл в этот.
Справочно: `arrival` — когда придёт заказ, размещённый сегодня; `days_short` и `lost_lead` — сколько дней и штук спроса будет потеряно до прихода; `excess_qty` — штук сверх нормы.

По поставщику: строки `items` с этим `supplier_id`, его заказ — `supplier_orders`, надёжность — `reliability` (`fill_ratio` — доля привезённого, `uplift` — поправка), просрочки — `stale_po`, ожидаемые приходы — `arrivals`. Что изменилось с прошлого прогона — `out/diff.json` (`new_orders`, `dropped_orders`, `changed`, `new_risks`). Пояснения ИИ к рисковым SKU — `out/note.json` → `sku_notes` (это текст, числа сверяй с `plan.json`). Точность прогноза — `out/backtest.json`: главное — `wape_sku_model` против `wape_sku_naive` (ошибка по SKU, меньше — лучше), по сумме категории — `wape_model` / `wape_naive`; `new_sku_actual` — продажи новинок после среза, в оценку не входят.

## 3. Собрать заказ поставщику

Готовые выгрузки после прогона — `site/exports/` (Excel и CSV, по поставщикам и общий). Если нужен свой срез — бери `supplier_orders` из `out/plan.json` (поля `supplier_label`, `sku_id`, `order_qty`, `weight_kg`, `order_by`, `expected_arrival`) и сохраняй, например, в `out/order_<SUP>.csv` через pandas. Позиции без поставщика идут с `supplier_id = "—"`.

## 4. Изменить параметр и показать разницу

Все параметры с комментариями — в `config.toml`. Чтобы сравнить, не портя историю и `out/`, считай вариант в отдельные папки:

```bash
mkdir -p .cache && rm -rf .cache/history_try   # папки может не быть; старая копия истории не нужна
cp config.toml .cache/config_try.toml          # затем правь параметр в копии
cp -r history .cache/history_try
$PY -m procurement run --no-ai --config .cache/config_try.toml \
  --history .cache/history_try --out .cache/out_try --site .cache/site_try
```

Покажи таблицу «было → стало» по `kpi` из `out/plan.json` и `.cache/out_try/plan.json` (`order_now_positions`, `order_now_qty`, `order_now_suppliers`, `deficit_unavoidable`, `deficit_if_late`, `excess`, `review_price`, `dead`) и 5–10 SKU с самым большим изменением `order_qty`. Если пользователь решит оставить новое значение — перенеси его в `config.toml` и пересчитай обычным `run`.
