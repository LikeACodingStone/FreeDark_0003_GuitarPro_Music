#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

action="${1:-run}"
case "$action" in
  --dry-run)
    action="dry-run"
    shift
    ;;
  run|restart|stop|status|dry-run|reset-account)
    shift || true
    ;;
  --*)
    action="run"
    ;;
  *)
    echo "Unknown action: $action"
    echo "Usage: ./deploy.sh [run|restart|stop|status|dry-run|reset-account]"
    exit 1
    ;;
esac

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed or not in PATH."
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is not available. Please install the docker compose plugin."
  exit 1
fi

if [ ! -f ".env" ] && [[ "$action" == "reset-account" ]]; then
  umask 077
  : > ".env"
elif [ ! -f ".env" ]; then
  echo "Creating .env. Telegram login password will not be stored."
  read -r -p "TG_API_ID: " tg_api_id
  read -r -s -p "TG_API_HASH: " tg_api_hash
  echo ""
  read -r -p "TG_PHONE (optional, include country code): " tg_phone

  tg_api_id="$(printf '%s' "$tg_api_id" | tr -d '[:space:]')"
  tg_api_hash="$(printf '%s' "$tg_api_hash" | tr -d '[:space:]')"
  tg_phone="$(printf '%s' "$tg_phone" | xargs)"

  if [[ ! "$tg_api_id" =~ ^[0-9]+$ ]]; then
    echo "TG_API_ID must be the numeric api_id from https://my.telegram.org/apps."
    exit 1
  fi

  if [[ ! "$tg_api_hash" =~ ^[0-9a-fA-F]{32}$ ]]; then
    echo "TG_API_HASH must be the 32-character api_hash from https://my.telegram.org/apps, not a bot token."
    exit 1
  fi

  umask 077
  {
    echo "TG_API_ID=${tg_api_id}"
    echo "TG_API_HASH=${tg_api_hash}"
    echo "TG_PHONE=${tg_phone}"
    echo "TG_BOT_USERNAME=SQMP3"
    echo ""
    echo "SESSION_TTL_HOURS=72"
    echo "MIN_DELAY=60"
    echo "MAX_DELAY=150"
    echo "RESPONSE_TIMEOUT=120"
    echo "LONG_REST_EVERY_MINUTES=120"
    echo "LONG_REST_MIN_MINUTES=30"
    echo "LONG_REST_MAX_MINUTES=90"
    echo "STOP_ON_SEND_BLOCKED=1"
    echo "SEND_BLOCKED_COOLDOWN_HOURS=6"
    echo "SUCCESS_CONFIRM_EVERY=500"
  } > ".env"
fi

mkdir -p TMDownload telegram_session

if [[ "$action" == "status" ]]; then
  docker compose ps -a
  exit 0
fi

if [[ "$action" == "stop" ]]; then
  docker compose down --remove-orphans
  exit 0
fi

if [[ "$action" == "restart" ]]; then
  echo "--> Stopping old containers..."
  docker compose down --remove-orphans
  action="run"
fi

echo "--> Building image..."
docker compose build

if [[ "$action" == "dry-run" ]]; then
  echo "--> Building pending list only. No Telegram login or download."
  docker compose run --rm telegram-downloader python /app/telegram_downloader.py --dry-run "$@"
elif [[ "$action" == "reset-account" ]]; then
  echo "--> Resetting Telegram account settings. No download will start."
  docker compose run --rm telegram-downloader python /app/telegram_downloader.py --reset-account-only "$@"
elif [[ "$action" == "run" ]]; then
  echo "--> Starting download task. Press Ctrl+C to stop safely."
  docker compose run --rm telegram-downloader python /app/telegram_downloader.py "$@"
else
  echo "Unknown action: $action"
  echo "Usage: ./deploy.sh [run|restart|stop|status|dry-run|reset-account]"
  exit 1
fi
