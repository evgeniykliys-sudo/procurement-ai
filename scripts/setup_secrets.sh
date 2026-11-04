#!/usr/bin/env bash
# Мастер настройки секретов: бот Telegram, chat id, ключ DeepSeek → секреты GitHub Actions
# (и, по желанию, локальный .env). Значения вводятся с клавиатуры без эха и не печатаются.
# Запуск из корня репозитория:  bash scripts/setup_secrets.sh [владелец/репозиторий]
set -euo pipefail

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ask()  { local reply; read -r -p "$1 [y/N] " reply; [[ "$reply" =~ ^[YyДд] ]]; }
die()  { printf 'Ошибка: %s\n' "$*" >&2; exit 1; }
read_secret() {  # read_secret ПЕРЕМЕННАЯ "подсказка" — ввод без эха
  local __value
  read -r -s -p "$2: " __value; echo
  [[ -n "$__value" ]] || die "пустое значение"
  printf -v "$1" '%s' "$__value"
}

command -v gh >/dev/null   || die "нужен GitHub CLI: https://cli.github.com (затем gh auth login)"
command -v curl >/dev/null || die "нужен curl"
gh auth status >/dev/null 2>&1 || die "GitHub CLI не авторизован — выполните: gh auth login"

REPO="${1:-$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)}"
[[ -n "$REPO" ]] || die "не найден репозиторий — передайте его аргументом: владелец/репозиторий"
say "Репозиторий: $REPO"

PY=""
for c in .venv/Scripts/python.exe .venv/bin/python python3 python; do
  if command -v "$c" >/dev/null 2>&1 || [[ -x "$c" ]]; then PY="$c"; break; fi
done
[[ -n "$PY" ]] || die "нужен Python (для разбора ответа Telegram)"

# --- 1. Бот Telegram ---
say "Шаг 1. Бот Telegram"
cat <<'TXT'
  1) Откройте в Telegram @BotFather, отправьте /newbot.
  2) Придумайте имя и логин бота (логин заканчивается на bot).
  3) BotFather пришлёт токен вида 123456789:AA... — скопируйте его.
TXT
read_secret TG_TOKEN "Вставьте токен бота (ввод не отображается)"

# --- 2. chat id ---
say "Шаг 2. Chat id"
echo "  Откройте своего бота в Telegram, нажмите «Запустить» (/start) и отправьте любое сообщение."
echo "  Для группы: добавьте бота в группу и напишите в ней сообщение."
read -r -p "  Готово? Нажмите Enter… " _
# Токен передаётся curl через stdin (-K -), а не аргументом — не виден в списке процессов.
UPDATES="$(printf 'url = "https://api.telegram.org/bot%s/getUpdates"\n' "$TG_TOKEN" | curl -sS -K - || true)"
CHAT_ID="$(printf '%s' "$UPDATES" | "$PY" -c '
import json, sys
try:
    data = json.load(sys.stdin)
except ValueError:
    sys.exit(2)
if not data.get("ok"):
    sys.exit(3)
ids = []
for upd in data.get("result", []):
    msg = upd.get("message") or upd.get("channel_post") or upd.get("my_chat_member") or {}
    chat = msg.get("chat") or {}
    if "id" in chat:
        ids.append(chat["id"])
if ids:
    print(ids[-1])
' || true)"
unset UPDATES
if [[ -z "$CHAT_ID" ]]; then
  echo "  Не удалось получить chat id: токен неверный или боту ещё никто не написал."
  echo "  Напишите боту сообщение и запустите мастер ещё раз — или введите chat id вручную."
  read -r -p "  Chat id (Enter — выйти): " CHAT_ID
  [[ -n "$CHAT_ID" ]] || exit 1
fi
echo "  Chat id: $CHAT_ID"

# --- 3. DeepSeek ---
say "Шаг 3. Ключ DeepSeek"
echo "  Ключ — на https://platform.deepseek.com → API keys (вида sk-...)."
read_secret DS_KEY "Вставьте ключ DeepSeek (ввод не отображается)"

# --- 4. Секреты GitHub ---
say "Шаг 4. Секреты GitHub Actions"
# gh secret set читает значение со stdin — оно не попадает ни в аргументы, ни в вывод.
printf '%s' "$TG_TOKEN" | gh secret set TELEGRAM_BOT_TOKEN --repo "$REPO"
printf '%s' "$CHAT_ID"  | gh secret set TELEGRAM_CHAT_ID   --repo "$REPO"
printf '%s' "$DS_KEY"   | gh secret set DEEPSEEK_API_KEY   --repo "$REPO"
echo "  Записаны: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DEEPSEEK_API_KEY"

# --- 5. Локальный .env ---
if ask "Записать те же значения в локальный .env (он в .gitignore)?"; then
  [[ -f .env ]] || cp .env.example .env
  TG_TOKEN="$TG_TOKEN" CHAT_ID="$CHAT_ID" DS_KEY="$DS_KEY" "$PY" - <<'PY'
import os
from pathlib import Path
values = {"TELEGRAM_BOT_TOKEN": os.environ["TG_TOKEN"], "TELEGRAM_CHAT_ID": os.environ["CHAT_ID"],
          "DEEPSEEK_API_KEY": os.environ["DS_KEY"]}
path = Path(".env")
lines = path.read_text(encoding="utf-8").splitlines()
out, seen = [], set()
for line in lines:
    key = line.split("=", 1)[0].strip()
    if key in values:
        out.append(f"{key}={values[key]}")
        seen.add(key)
    else:
        out.append(line)
out += [f"{k}={v}" for k, v in values.items() if k not in seen]
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
  echo "  .env обновлён."
fi
unset TG_TOKEN DS_KEY

# --- 6. Pages и первый запуск ---
say "Шаг 5. GitHub Pages"
echo "  Settings → Pages → Build and deployment → Source: GitHub Actions."
if ask "Включить Pages с источником «GitHub Actions» сейчас через gh?"; then
  if gh api -X POST "repos/$REPO/pages" -f build_type=workflow >/dev/null 2>&1 \
     || gh api -X PUT "repos/$REPO/pages" -f build_type=workflow >/dev/null 2>&1; then
    echo "  Pages включены."
  else
    echo "  Не получилось — включите вручную по пути выше."
  fi
fi

say "Шаг 6. Первый запуск"
if ask "Запустить расчёт в GitHub Actions сейчас?"; then
  gh workflow run plan.yml --repo "$REPO"
  echo "  Запущено. Ход — вкладка Actions или: gh run watch --repo $REPO"
fi
say "Готово."
