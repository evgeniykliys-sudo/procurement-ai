# F1 — Правки по ревью ветки

**Требования:** R26, G03, R19, R31i, R01, R20
**Зависит от:** —
**Зона:** `.github/workflows/` · `procurement/__main__.py` · `procurement/plan.py` · `procurement/supply.py` · `procurement/notify.py` · `.claude/skills/` · `README.md` · `prompts/` · `docs/` · `requirements.txt` · `tests/`
**Волна:** 5
**Ревью:** да — ремонт ветки
**Модель:** сильная

## Что должно заработать

Первый живой прогон в GitHub Actions проходит: ссылка в дайджесте верная при всех триггерах, дайджест уходит после деплоя, время пересчёта — по Новосибирску, права минимальные. Скилл и README работают на Linux/macOS и Windows. Нет «исполнения 0%» из-за несопоставленных приходов. README с картинкой дашборда.

## Из брифа, дословно

> «Например, ежедневно или по запросу запускать анализ и получать актуальные рекомендации»
> «Разница qty_ordered / qty_confirmed / qty_received — материал для модели надёжности поставщика»
> «В видео покажите … файлы CLAUDE.md, Skills, промпты»

## Разделы спецификации

Истории 15, 33, 36, 37, 39, 40.

## Критерии приёмки

- [ ] .github/workflows/plan.yml:54 — SITE_URL берётся из `${GITHUB_REPOSITORY#*/}` или из `steps.<id>.outputs.base_url` шага configure-pages и непуст при всех трёх триггерах
- [ ] procurement/plan.py:516 + plan.yml:49-55 — на раннере время пересчёта выводится в Asia/Novosibirsk (`TZ: Asia/Novosibirsk` в env шага или явная зона в коде)
- [ ] plan.yml:18-21 — на верхнем уровне `permissions: {}` или `contents: read`; у `plan` только `contents: write`; у `deploy` только `pages: write` + `id-token: write`
- [ ] Дайджест уходит после job `deploy` (отдельный шаг/job; CLI-команда `python -m procurement notify` читает out/ или артефакт — на выбор исполнителя), ссылка ведёт на уже обновлённый сайт; секреты Telegram — только у этого шага
- [ ] README.md:15 — файл `docs/img/dashboard.png` лежит в репозитории (скопировать `.cache/shots/summary_1920.png`), картинка не битая
- [ ] README.md:56 и prompts/analyst.md:14 — обе строки говорят «среднее за последние 12 мес», а не «как год назад»
- [ ] .claude/skills/zakupki/SKILL.md:21,22,32,61 и README.md:24-34 — скилл и README один раз называют оба пути (или переменную `PY`), команды работают на Linux/macOS и Windows
- [ ] SKILL.md:59-60 — перед копированием `mkdir -p .cache && rm -rf .cache/history_try`
- [ ] procurement/supply.py:132-140 — при Σ сопоставленного прихода = 0 `fill_ratio` = null («нет данных»), `uplift` = 1; дашборд показывает «нет данных», а не 0%
- [ ] plan.py:359,368 — при повторном заказе 0 шт. переходить к следующей дате пересечения страхового запаса, пока она в окне календаря
- [ ] requirements.txt — версии закреплены на проверенных (`==` текущие установленные в .venv)
- [ ] `pytest -q` зелёный; `run --no-ai` на реальных данных проходит
