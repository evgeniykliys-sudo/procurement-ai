window.STATE =
{
  "version": 2,
  "slug": "procurement-ai",
  "dir": "2026-11-04-procurement-ai--wip",
  "title": "ИИ-система планирования закупок (тестовое)",
  "mode": "interview",
  "depth": "normal",
  "tier": "T2",
  "briefFile": "2026-11-04-brief.md",
  "memoryFile": "AGENTS.md",
  "memoryOwner": "autopilot",
  "skillDir": "C:/Users/JG777/Documents/Projects/.agents/skills/autopilot",
  "baseCommit": null,
  "startedAt": "2026-11-04T10:40:02+07:00",
  "updatedAt": "2026-11-04T10:59:38+07:00",
  "finishedAt": null,
  "stages": [
    {
      "id": "preflight",
      "status": "done",
      "startedAt": "2026-11-04T10:40:02+07:00",
      "finishedAt": "2026-11-04T10:40:47+07:00"
    },
    {
      "id": "manifest",
      "status": "done",
      "startedAt": "2026-11-04T10:40:47+07:00",
      "finishedAt": "2026-11-04T10:40:47+07:00"
    },
    {
      "id": "briefing",
      "status": "done",
      "startedAt": "2026-11-04T10:40:47+07:00",
      "finishedAt": "2026-11-04T10:52:29+07:00"
    },
    {
      "id": "spec",
      "status": "done",
      "startedAt": "2026-11-04T10:52:29+07:00",
      "finishedAt": "2026-11-04T10:59:36+07:00"
    },
    {
      "id": "plan",
      "status": "active",
      "startedAt": "2026-11-04T10:59:36+07:00"
    },
    {
      "id": "build",
      "status": "pending"
    },
    {
      "id": "review",
      "status": "pending"
    },
    {
      "id": "final",
      "status": "pending"
    }
  ],
  "requirements": {
    "total": 38,
    "done": 0,
    "inTicket": 38,
    "inSpec": 0,
    "placeholder": 0,
    "deferred": 0,
    "dropped": 0
  },
  "tickets": [
    {
      "id": "01",
      "title": "Фундамент: данные, прогноз спроса, поставки",
      "requirements": [
        "R13",
        "R17",
        "R18",
        "R08",
        "R09",
        "R11",
        "R12",
        "R19",
        "R33i",
        "R34i",
        "R15"
      ],
      "blockedBy": [],
      "wave": 1,
      "zone": [
        "procurement/config.py",
        "procurement/data.py",
        "procurement/demand.py",
        "procurement/supply.py",
        "procurement/__init__.py",
        "config.toml",
        "requirements.txt",
        "tests/conftest.py",
        "tests/test_data.py",
        "tests/test_demand.py",
        "tests/test_supply.py",
        "tests/test_smoke.py"
      ],
      "review": "да — фундамент",
      "model": "сильная",
      "status": "pending",
      "retries": 0,
      "repairs": 0
    },
    {
      "id": "02",
      "title": "Решение о закупке, бэктест, история, CLI",
      "requirements": [
        "R01",
        "R02",
        "R03",
        "R04",
        "R05",
        "R06",
        "R07",
        "R10",
        "R14",
        "R16",
        "R27",
        "G01",
        "R26"
      ],
      "blockedBy": [
        "01"
      ],
      "wave": 2,
      "zone": [
        "procurement/plan.py",
        "procurement/backtest.py",
        "procurement/history.py",
        "procurement/__main__.py",
        "procurement/site.py",
        "procurement/ai.py",
        "procurement/notify.py",
        "history/",
        "tests/test_plan.py",
        "tests/test_backtest.py",
        "tests/test_history.py",
        "tests/test_integration.py"
      ],
      "review": "да — фундамент",
      "model": "сильная",
      "status": "pending",
      "retries": 0,
      "repairs": 0
    },
    {
      "id": "03",
      "title": "Дашборд",
      "requirements": [
        "R20",
        "R21",
        "R22",
        "G02",
        "R35i",
        "R33i",
        "R34i"
      ],
      "blockedBy": [
        "02"
      ],
      "wave": 3,
      "zone": [
        "procurement/site.py",
        "procurement/templates/",
        "procurement/static/",
        "tests/test_site.py"
      ],
      "review": "нет",
      "model": "сильная",
      "status": "pending",
      "retries": 0,
      "repairs": 0
    },
    {
      "id": "04",
      "title": "ИИ-аналитик, Telegram, GitHub Actions, скилл Claude Code",
      "requirements": [
        "R31i",
        "R32i",
        "R25",
        "R26",
        "G03",
        "R01"
      ],
      "blockedBy": [
        "02"
      ],
      "wave": 3,
      "zone": [
        "procurement/ai.py",
        "procurement/notify.py",
        "prompts/",
        ".github/workflows/",
        ".claude/skills/",
        "docs/",
        "scripts/",
        "README.md",
        ".env.example",
        "tests/test_ai.py",
        "tests/test_notify.py"
      ],
      "review": "да — секреты и сообщения третьим сторонам",
      "model": "сильная",
      "status": "pending",
      "retries": 0,
      "repairs": 0
    },
    {
      "id": "05",
      "title": "Видео демонстрации и сдача",
      "requirements": [
        "R23",
        "R24",
        "R28",
        "R29",
        "R30"
      ],
      "blockedBy": [
        "03",
        "04"
      ],
      "wave": 4,
      "zone": [
        "video/",
        "docs/submission.md"
      ],
      "review": "нет",
      "model": "обычная",
      "status": "pending",
      "retries": 0,
      "repairs": 0
    }
  ],
  "tests": null,
  "debt": {
    "placeholders": [],
    "assumptions": [],
    "emptyEnv": []
  },
  "additions": [],
  "coverage": {
    "found": 7,
    "fixed": 7,
    "deferred": 0,
    "items": []
  },
  "concerns": [],
  "blind": null,
  "beats": [
    "2026-11-04T10:40:02+07:00",
    "2026-11-04T10:40:28+07:00",
    "2026-11-04T10:40:47+07:00",
    "2026-11-04T10:41:35+07:00",
    "2026-11-04T10:49:23+07:00",
    "2026-11-04T10:49:24+07:00",
    "2026-11-04T10:52:29+07:00",
    "2026-11-04T10:57:18+07:00",
    "2026-11-04T10:59:36+07:00",
    "2026-11-04T10:59:37+07:00",
    "2026-11-04T10:59:38+07:00"
  ],
  "rev": 14,
  "waiting": null,
  "waits": [
    {
      "kind": "briefing",
      "from": "2026-11-04T10:41:35+07:00",
      "to": "2026-11-04T10:49:23+07:00"
    },
    {
      "kind": "briefing",
      "from": "2026-11-04T10:49:24+07:00",
      "to": "2026-11-04T10:52:29+07:00"
    }
  ],
  "log": [
    {
      "t": "2026-11-04T10:40:02+07:00",
      "k": "init"
    },
    {
      "t": "2026-11-04T10:40:47+07:00",
      "k": "stage",
      "id": "manifest",
      "act": "start"
    },
    {
      "t": "2026-11-04T10:40:47+07:00",
      "k": "stage",
      "id": "briefing",
      "act": "start"
    },
    {
      "t": "2026-11-04T10:41:35+07:00",
      "k": "ask",
      "kind": "briefing",
      "note": "раунд 1 — хостинг, ИИ-слой, видео, параметры закупки, бэктест, заказы по поставщикам, график"
    },
    {
      "t": "2026-11-04T10:49:23+07:00",
      "k": "answered",
      "kind": "briefing"
    },
    {
      "t": "2026-11-04T10:49:24+07:00",
      "k": "ask",
      "kind": "briefing",
      "note": "раунд 2 — обновление данных, история прогонов, видео, Telegram-дайджест"
    },
    {
      "t": "2026-11-04T10:52:29+07:00",
      "k": "answered",
      "kind": "briefing"
    },
    {
      "t": "2026-11-04T10:52:29+07:00",
      "k": "gate",
      "g": "G1",
      "ok": false,
      "open": 38,
      "total": 38
    },
    {
      "t": "2026-11-04T10:52:29+07:00",
      "k": "stage",
      "id": "spec",
      "act": "start"
    },
    {
      "t": "2026-11-04T10:57:18+07:00",
      "k": "gate",
      "g": "G2",
      "ok": true,
      "found": 7,
      "fixed": 7,
      "deferred": 0
    },
    {
      "t": "2026-11-04T10:59:36+07:00",
      "k": "stage",
      "id": "plan",
      "act": "start"
    },
    {
      "t": "2026-11-04T10:59:37+07:00",
      "k": "tier",
      "v": "T2"
    },
    {
      "t": "2026-11-04T10:59:37+07:00",
      "k": "tickets",
      "n": 5
    },
    {
      "t": "2026-11-04T10:59:38+07:00",
      "k": "gate",
      "g": "G3",
      "ok": true,
      "problems": 0
    }
  ],
  "reqList": [
    {
      "id": "R01",
      "text": "«разработать ИИ-систему, которая автоматизирует процесс анализа и планирования закупок»",
      "status": "in-ticket"
    },
    {
      "id": "R02",
      "text": "«какие товары необходимо закупить»",
      "status": "in-ticket"
    },
    {
      "id": "R03",
      "text": "«в каком количестве разместить заказ»",
      "status": "in-ticket"
    },
    {
      "id": "R04",
      "text": "«на какой период хватит текущих запасов с учётом уже размещённых заказов»",
      "status": "in-ticket"
    },
    {
      "id": "R05",
      "text": "«когда необходимо размещать следующий заказ»",
      "status": "in-ticket"
    },
    {
      "id": "R06",
      "text": "«по каким позициям есть риск дефицита»",
      "status": "in-ticket"
    },
    {
      "id": "R07",
      "text": "«или избыточных запасов»",
      "status": "in-ticket"
    },
    {
      "id": "R08",
      "text": "«учитывать … историю и динамику продаж, популярность товаров»",
      "status": "in-ticket"
    },
    {
      "id": "R09",
      "text": "«Учтена сезонность» — space_heaters (октябрь), string_trimmers (май), sanitary_faucets (ровный)",
      "status": "in-ticket"
    },
    {
      "id": "R10",
      "text": "«решение принимается против прогноза спроса на горизонте „плечо + период покрытия“»",
      "status": "in-ticket"
    },
    {
      "id": "R11",
      "text": "«180 — верхняя граница, а не константа» (плечо по факту из purchase_orders)",
      "status": "in-ticket"
    },
    {
      "id": "R12",
      "text": "«Учтено уже заказанное … их количество нельзя заказывать повторно»",
      "status": "in-ticket"
    },
    {
      "id": "R13",
      "text": "«текущие складские остатки»",
      "status": "in-ticket"
    },
    {
      "id": "R14",
      "text": "«Товар с низкой наценкой не должен попадать в закупку автоматически — он идёт в отдельный список»",
      "status": "in-ticket"
    },
    {
      "id": "R15",
      "text": "«MIN_MARKUP_PCT = 25» (параметр)",
      "status": "in-ticket"
    },
    {
      "id": "R16",
      "text": "«378 SKU — это мёртвый ассортимент, его тоже надо распознать»",
      "status": "in-ticket"
    },
    {
      "id": "R17",
      "text": "«Месяц с нулевыми продажами при нулевом остатке — это упущенный спрос»",
      "status": "in-ticket"
    },
    {
      "id": "R18",
      "text": "«partial_month = 1 — … в расчёт средних не брать»",
      "status": "in-ticket"
    },
    {
      "id": "R19",
      "text": "«Разница qty_ordered / qty_confirmed / qty_received — материал для модели надёжности поставщика»",
      "status": "in-ticket"
    },
    {
      "id": "R20",
      "text": "«Ссылку на готовый дашборд»",
      "status": "in-ticket"
    },
    {
      "id": "R21",
      "text": "«наглядно представлены результаты анализа и рекомендации системы: что необходимо заказать, в каком количестве и когда»",
      "status": "in-ticket"
    },
    {
      "id": "R22",
      "text": "«по дашборду можно было получить полную картину предстоящих закупок и принять решение»",
      "status": "in-ticket"
    },
    {
      "id": "R23",
      "text": "«Видео демонстрации системы»",
      "status": "in-ticket"
    },
    {
      "id": "R24",
      "text": "«покажите используемый инструмент (Claude Code или другой), … структуру проекта, файлы CLAUDE.md, Skills, промпты»",
      "status": "in-ticket"
    },
    {
      "id": "R25",
      "text": "«система, которую можно запускать повторно при обновлении информации»",
      "status": "in-ticket"
    },
    {
      "id": "R26",
      "text": "«ежедневно или по запросу запускать анализ и получать актуальные рекомендации»",
      "status": "in-ticket"
    },
    {
      "id": "R27",
      "text": "«как изменились потребности в закупках»",
      "status": "in-ticket"
    },
    {
      "id": "R28",
      "text": "«Срок выполнения — 48 часов» (до 2026-11-05 22:48 НСК)",
      "status": "in-ticket"
    },
    {
      "id": "R29",
      "text": "«Готовое решение необходимо прислать ответным сообщением сюда, в Telegram»",
      "status": "in-ticket"
    },
    {
      "id": "R30",
      "text": "«используй скилл /autopilot и /grilling для этого проекта»",
      "status": "in-ticket"
    },
    {
      "id": "R31i",
      "text": "(подразумевается) «ИИ» — в системе должна быть видна работа LLM, не только формулы",
      "status": "in-ticket"
    },
    {
      "id": "R32i",
      "text": "(подразумевается) дашборд доступен по ссылке извне (хостинг)",
      "status": "in-ticket"
    },
    {
      "id": "R33i",
      "text": "(подразумевается) возвраты qty_returned не вычтены из продаж — учесть в спросе",
      "status": "in-ticket"
    },
    {
      "id": "R34i",
      "text": "(подразумевается) SKU без истории приходов (unknown, 153 шт.) — плечо и решение для них",
      "status": "in-ticket"
    },
    {
      "id": "R35i",
      "text": "(подразумевается) объём заказа в кг/контейнерах («weight_kg — для расчёта объёма контейнера»)",
      "status": "in-ticket"
    },
    {
      "id": "G01",
      "text": "«Q5-Делаем» — бэктест точности прогноза против реальных продаж и против заказа по среднему",
      "status": "in-ticket"
    },
    {
      "id": "G02",
      "text": "«Закупщик размещает заказ поставщику, а не по одному SKU» — заказ поставщику: позиции, кол-во, вес, дата, выгрузка",
      "status": "in-ticket"
    },
    {
      "id": "G03",
      "text": "Q11 «ок» — дайджест в Telegram после планового прогона (новый бот, в личку)",
      "status": "in-ticket"
    }
  ],
  "gates": {
    "G1": {
      "ok": false,
      "at": "2026-11-04T10:52:29+07:00",
      "open": 38,
      "total": 38
    },
    "G2": {
      "ok": true,
      "at": "2026-11-04T10:57:18+07:00",
      "found": 7,
      "fixed": 7,
      "deferred": 0
    },
    "G3": {
      "ok": true,
      "at": "2026-11-04T10:59:38+07:00",
      "problems": 0
    }
  },
  "result": null
}
