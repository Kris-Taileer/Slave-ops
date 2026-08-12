#!/usr/bin/env bash
#
# firegex-setup.sh — быстрый развёртывание Firegex (https://github.com/Pwnzer0tt1/firegex)
# на vulnbox для Attack-Defense CTF.
#
#   sudo ./firegex-setup.sh                       # поставить и запустить (пароль сгенерится)
#   sudo ./firegex-setup.sh -w mypassword -p 4444 # свой пароль и порт
#   sudo ./firegex-setup.sh status|stop|restart|logs|update
#
set -Eeuo pipefail

# ------------------------------- настройки по умолчанию -------------------------------
FIREGEX_DIR="${FIREGEX_DIR:-/opt/firegex}"
FIREGEX_REPO="${FIREGEX_REPO:-https://github.com/Pwnzer0tt1/firegex.git}"
PORT="${FIREGEX_PORT:-4444}"
PASSWORD="${FIREGEX_PASSWORD:-}"
HOST=""                 # --host: на каком IP слушать (по умолчанию из конфига firegex)
ALLOWED_IPS=""          # --allowed-ips: CIDR, которым разрешён доступ к веб-морде
MODE="prebuilt"         # prebuilt | build | standalone
INSTALL_DEPS=1
ACTION="start"
CRED_FILE=""

# ------------------------------------- утилиты ---------------------------------------
c_red=$'\033[1;31m'; c_grn=$'\033[1;32m'; c_ylw=$'\033[1;33m'; c_cyn=$'\033[1;36m'; c_off=$'\033[0m'
log()  { printf '%s[*]%s %s\n' "$c_cyn" "$c_off" "$*"; }
ok()   { printf '%s[+]%s %s\n' "$c_grn" "$c_off" "$*"; }
warn() { printf '%s[!]%s %s\n' "$c_ylw" "$c_off" "$*" >&2; }
die()  { printf '%s[-]%s %s\n' "$c_red" "$c_off" "$*" >&2; exit 1; }
has()  { command -v "$1" >/dev/null 2>&1; }

usage() {
  cat <<'EOF'
firegex-setup.sh [действие] [опции]

Действия:
  start (по умолчанию)   поставить зависимости, склонировать/обновить репу, запустить
  stop                   остановить firegex
  restart                перезапустить
  status                 показать состояние
  logs                   показать логи
  update                 git pull + перезапуск
  clean                  остановить и удалить volume со всеми настройками (!)

Опции:
  -p, --port N           порт веб-интерфейса (по умолчанию 4444)
  -w, --password PSW     пароль (по умолчанию генерируется и печатается в конце)
      --host IP          адрес, на котором слушать
      --allowed-ips CIDR список CIDR, которым разрешён доступ к веб-морде
      --build            собрать образ из исходников вместо готового (дольше)
      --standalone       режим без docker (rootless / нет докера)
  -d, --dir PATH         куда ставить (по умолчанию /opt/firegex)
      --no-deps          не ставить зависимости самому
  -h, --help             эта справка

Переменные окружения: FIREGEX_DIR, FIREGEX_PORT, FIREGEX_PASSWORD, FIREGEX_REPO
EOF
}

# --------------------------------- разбор аргументов ---------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    start|stop|restart|status|logs|update|clean) ACTION="$1"; shift ;;
    -p|--port)        PORT="${2:?нужно значение порта}"; shift 2 ;;
    -w|--password)    PASSWORD="${2:?нужен пароль}"; shift 2 ;;
    --host)           HOST="${2:?нужен адрес}"; shift 2 ;;
    --allowed-ips)    ALLOWED_IPS="${2:?нужен список CIDR}"; shift 2 ;;
    --build)          MODE="build"; shift ;;
    --standalone)     MODE="standalone"; shift ;;
    -d|--dir)         FIREGEX_DIR="${2:?нужен путь}"; shift 2 ;;
    --no-deps)        INSTALL_DEPS=0; shift ;;
    -h|--help)        usage; exit 0 ;;
    *) die "неизвестный аргумент: $1 (см. --help)" ;;
  esac
done
CRED_FILE="$FIREGEX_DIR/.firegex-credentials"

# ----------------------------------- предпроверки ------------------------------------
[[ "$(uname -s)" == "Linux" ]] || die "Firegex работает только на Linux (нужны nftables/NFQUEUE). Запускай на vulnbox."
[[ $EUID -eq 0 ]] || die "нужен root: sudo $0 $*"

pkg_install() {
  if   has apt-get; then DEBIAN_FRONTEND=noninteractive apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@"
  elif has dnf;     then dnf install -y -q "$@"
  elif has yum;     then yum install -y -q "$@"
  elif has pacman;  then pacman -Sy --noconfirm --needed "$@"
  elif has apk;     then apk add --no-cache "$@"
  elif has zypper;  then zypper -n install "$@"
  else return 1; fi
}

install_deps() {
  local missing=()
  for b in git curl python3; do has "$b" || missing+=("$b"); done
  if ((${#missing[@]})); then
    log "ставлю: ${missing[*]}"
    pkg_install "${missing[@]}" || die "не смог поставить ${missing[*]} — поставь руками и перезапусти с --no-deps"
  fi

  # nftables-модули ядра нужны фильтру регексов (NFQUEUE)
  modprobe nfnetlink_queue 2>/dev/null || true
  modprobe nft_queue       2>/dev/null || true

  if [[ "$MODE" == "standalone" ]]; then
    ok "standalone-режим — docker не нужен"
    return
  fi

  if ! has docker; then
    log "docker не найден, ставлю через get.docker.com"
    curl -fsSL https://get.docker.com | sh || die "установка docker не удалась; попробуй --standalone"
  fi
  systemctl enable --now docker >/dev/null 2>&1 || true
  docker info >/dev/null 2>&1 || die "docker есть, но демон не отвечает (systemctl start docker)"

  if ! docker compose version >/dev/null 2>&1 && ! has docker-compose; then
    log "ставлю docker compose plugin"
    pkg_install docker-compose-plugin || pkg_install docker-compose \
      || die "нет docker compose — поставь плагин руками или используй --standalone"
  fi
  ok "docker готов"
}

fetch_repo() {
  if [[ -d "$FIREGEX_DIR/.git" ]]; then
    log "репа уже есть в $FIREGEX_DIR, обновляю"
    git -C "$FIREGEX_DIR" pull --ff-only || warn "git pull не прошёл, работаю на текущей версии"
  else
    log "клонирую firegex в $FIREGEX_DIR"
    mkdir -p "$(dirname "$FIREGEX_DIR")"
    git clone --depth 1 "$FIREGEX_REPO" "$FIREGEX_DIR" || die "клонирование не удалось"
  fi
  [[ -f "$FIREGEX_DIR/run.py" ]] || die "в $FIREGEX_DIR нет run.py — не похоже на репозиторий firegex"
}

gen_password() {
  if has openssl; then openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 20
  else tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20; fi
}

# run.py всегда запускаем из каталога репы — он там держит .firegex-conf.json
fg() { ( cd "$FIREGEX_DIR" && python3 run.py "$@" ); }

# флаг режима как массив: --prebuilt / --standalone / ничего (сборка из исходников)
MODE_FLAGS=()
case "$MODE" in
  prebuilt)   MODE_FLAGS=(--prebuilt) ;;
  standalone) MODE_FLAGS=(--standalone) ;;
esac
# --standalone нужен и командам stop/status/restart, а --prebuilt им не нужен
SFLAG=()
[[ "$MODE" == standalone ]] && SFLAG=(--standalone)

do_start() {
  ((INSTALL_DEPS)) && install_deps
  fetch_repo

  if [[ -z "$PASSWORD" ]]; then
    PASSWORD="$(gen_password)"
    warn "пароль не задан — сгенерировал новый"
  fi

  local args=(start ${MODE_FLAGS[@]+"${MODE_FLAGS[@]}"} --port "$PORT" --startup-psw "$PASSWORD")
  [[ -n "$HOST"        ]] && args+=(--host "$HOST")
  [[ -n "$ALLOWED_IPS" ]] && args+=(--allowed-ips "$ALLOWED_IPS")

  log "запускаю: python3 run.py ${args[*]//"$PASSWORD"/******}"
  fg "${args[@]}" || die "firegex не стартанул — смотри вывод выше"

  umask 077
  { echo "url=http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT"
    echo "port=$PORT"
    echo "password=$PASSWORD"
    echo "installed=$(date -Is)"; } > "$CRED_FILE"
  chmod 600 "$CRED_FILE"

  local ip; ip="$(hostname -I 2>/dev/null | awk '{print $1}')"; ip="${ip:-<ip-вулнбокса>}"
  echo
  ok "Firegex поднят"
  printf '    веб-морда : %shttp://%s:%s%s\n' "$c_cyn" "$ip" "$PORT" "$c_off"
  printf '    пароль    : %s%s%s\n' "$c_grn" "$PASSWORD" "$c_off"
  printf '    сохранено : %s\n' "$CRED_FILE"
  echo
  cat <<EOF
Дальше в UI:
  1. Regex filter  — добавь сервис (порт, proto, ipv4/6) и включи → трафик пойдёт через NFQUEUE.
  2. Правила пиши в base64-регексах, начинай с blacklist на флаг-формат эксплойтов, а не на весь ввод.
  3. Firewall      — базовые allow/deny, аналог ufw, если нужно закрыть лишние порты.
  4. Port hijack   — подменить порт сервиса на свой прокси.
Проверь, что снаружи открыт только $PORT для твоей команды (--allowed-ips CIDR), иначе морду заберут соперники.
EOF
}

case "$ACTION" in
  start)   do_start ;;
  stop)    fg stop    ${SFLAG[@]+"${SFLAG[@]}"} ;;
  restart) fg restart ${SFLAG[@]+"${SFLAG[@]}"} ;;
  status)  fg status  ${SFLAG[@]+"${SFLAG[@]}"} ;;
  logs)    fg start --logs ${MODE_FLAGS[@]+"${MODE_FLAGS[@]}"} ;;
  update)  fetch_repo; fg restart ${SFLAG[@]+"${SFLAG[@]}"} ;;
  clean)
    read -rp "удалить volume firegex со всеми настройками? [y/N] " a
    [[ "$a" =~ ^[yY]$ ]] || die "отменено"
    fg stop --clear ${SFLAG[@]+"${SFLAG[@]}"}
    ok "volume удалён" ;;
esac
