#!/usr/bin/env bash
#
# packmate-setup.sh — quick deployment of Packmate (https://gitlab.com/packmate/Packmate)
# for Attack-Defense CTFs. Vendored source lives in ./Packmate-master (unpacked from the
# upstream release), this script only handles bring-up + first-time configuration.
#
#   ./packmate-setup.sh                          # install and start, then ask which
#                                                 # services exist and wire flag in/out rules
#   ./packmate-setup.sh --interface game --local-ip 10.10.10.5
#   ./packmate-setup.sh configure                # re-run just the services/patterns wizard
#   ./packmate-setup.sh status|stop|restart|logs|update|clean
#
set -Eeuo pipefail

# ----------------------------------- defaults ----------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$SCRIPT_DIR/Packmate-master"
COMPOSE_FILE="$SRC_DIR/docker-compose.yml"
ENV_FILE="$SRC_DIR/.env"
CRED_FILE="$SCRIPT_DIR/.packmate-credentials"

WEB_LOGIN="${PACKMATE_WEB_LOGIN:-}"
WEB_PASSWORD="${PACKMATE_WEB_PASSWORD:-}"
DB_PASSWORD="${PACKMATE_DB_PASSWORD:-}"
INTERFACE="${PACKMATE_INTERFACE:-}"
LOCAL_IP="${PACKMATE_LOCAL_IP:-}"
BUILD=0
RUN_CONFIGURE=1
ACTION="start"
PORT=65000

# ----------------------------------- helpers -----------------------------------------
c_red=$'\033[1;31m'; c_grn=$'\033[1;32m'; c_ylw=$'\033[1;33m'; c_cyn=$'\033[1;36m'; c_off=$'\033[0m'
log()  { printf '%s[*]%s %s\n' "$c_cyn" "$c_off" "$*"; }
ok()   { printf '%s[+]%s %s\n' "$c_grn" "$c_off" "$*"; }
warn() { printf '%s[!]%s %s\n' "$c_ylw" "$c_off" "$*" >&2; }
die()  { printf '%s[-]%s %s\n' "$c_red" "$c_off" "$*" >&2; exit 1; }
has()  { command -v "$1" >/dev/null 2>&1; }

usage() {
  cat <<'EOF'
packmate-setup.sh [action] [options]

Actions:
  start (default)         bring the stack up, then run the services/patterns wizard
  configure                re-run just the wizard (add services mid-game, safe to repeat)
  stop                     stop packmate
  restart                  restart it
  status                   show container state
  logs                     show logs (interactive, Ctrl-C to quit)
  update                   pull fresh images + restart (prebuilt mode only)
  clean                    stop and delete the postgres volume with all data (!)

Options:
  --interface NAME        capture interface (e.g. game) — needed for LIVE mode
  --local-ip IP           local IP on that interface
  --web-login NAME        web UI login (default: generated)
  --web-password PSW      web UI password (default: generated)
  --build                 build the image from ./Packmate-master instead of pulling
                           registry.gitlab.com/packmate/packmate (slower, works offline
                           once the frontend submodule + deps are vendored)
  --no-configure           on start, skip the services/patterns wizard
  -h, --help               this help

Environment variables instead of flags: PACKMATE_INTERFACE, PACKMATE_LOCAL_IP,
PACKMATE_WEB_LOGIN, PACKMATE_WEB_PASSWORD, PACKMATE_DB_PASSWORD
EOF
}

# --------------------------------- argument parsing ----------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    start|configure|stop|restart|status|logs|update|clean) ACTION="$1"; shift ;;
    --interface)      INTERFACE="${2:?interface name required}"; shift 2 ;;
    --local-ip)       LOCAL_IP="${2:?ip required}"; shift 2 ;;
    --web-login)      WEB_LOGIN="${2:?login required}"; shift 2 ;;
    --web-password)   WEB_PASSWORD="${2:?password required}"; shift 2 ;;
    --build)          BUILD=1; shift ;;
    --no-configure)   RUN_CONFIGURE=0; shift ;;
    -h|--help)        usage; exit 0 ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
done

[[ -f "$COMPOSE_FILE" ]] || die "no docker-compose.yml in $SRC_DIR — Packmate-master wasn't vendored correctly"

# ---------------------------------- preflight checks ---------------------------------
check_deps() {
  local missing=()
  has docker || missing+=(docker)
  docker compose version >/dev/null 2>&1 || missing+=("docker compose plugin")
  has python3 || missing+=(python3)
  if ((${#missing[@]})); then
    die "missing dependencies: ${missing[*]} — install them and rerun"
  fi
  docker info >/dev/null 2>&1 || die "docker is installed but the daemon is not responding (systemctl start docker)"
}

gen_secret() {
  if has openssl; then openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c "${1:-20}"
  else tr -dc 'A-Za-z0-9' </dev/urandom | head -c "${1:-20}"; fi
}

detect_ip() {
  # best-effort default for --local-ip: primary IP on the given interface, else host default IP
  local iface="$1"
  if [[ -n "$iface" ]] && has ip; then
    ip -4 -o addr show dev "$iface" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1
  fi
}

# always run docker compose against the vendored compose file, from its own directory
# so a plain `.env` next to it is picked up automatically
dc() { ( cd "$SRC_DIR" && docker compose "$@" ); }

write_env() {
  [[ -n "$WEB_LOGIN" ]] || WEB_LOGIN="BinaryBears"
  if [[ -z "$WEB_PASSWORD" ]]; then
    WEB_PASSWORD="$(gen_secret 20)"
    warn "no web password given — generated a new one"
  fi
  if [[ -f "$ENV_FILE" ]]; then
    # reuse a previously generated DB password so the volume stays readable across reruns
    DB_PASSWORD="${DB_PASSWORD:-$(grep -m1 '^PACKMATE_DB_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)}"
  fi
  [[ -n "$DB_PASSWORD" ]] || DB_PASSWORD="$(gen_secret 32)"

  if [[ -z "$INTERFACE" ]]; then
    if [[ -t 0 ]]; then
      read -rp "Интерфейс с игровым трафиком (например game, eth0): " INTERFACE
    fi
    [[ -n "$INTERFACE" ]] || die "--interface is required for LIVE capture (or pass PACKMATE_INTERFACE)"
  fi
  if [[ -z "$LOCAL_IP" ]]; then
    LOCAL_IP="$(detect_ip "$INTERFACE")"
    if [[ -z "$LOCAL_IP" && -t 0 ]]; then
      read -rp "Локальный IP на интерфейсе $INTERFACE: " LOCAL_IP
    fi
    [[ -n "$LOCAL_IP" ]] || die "--local-ip is required (couldn't auto-detect it on $INTERFACE)"
  fi

  umask 077
  cat > "$ENV_FILE" <<EOF
PACKMATE_LOCAL_IP=$LOCAL_IP
PACKMATE_INTERFACE=$INTERFACE
PACKMATE_MODE=LIVE
PACKMATE_WEB_LOGIN=$WEB_LOGIN
PACKMATE_WEB_PASSWORD=$WEB_PASSWORD
PACKMATE_DB_PASSWORD=$DB_PASSWORD
BUILD_TAG=latest
EOF
  ok "wrote $ENV_FILE (interface=$INTERFACE, local-ip=$LOCAL_IP)"
}

wait_healthy() {
  log "waiting for packmate to come up..."
  local i
  for ((i = 0; i < 60; i++)); do
    if curl -fsS -o /dev/null -u "$WEB_LOGIN:$WEB_PASSWORD" "http://127.0.0.1:$PORT/api/service/" 2>/dev/null; then
      ok "packmate is up"
      return 0
    fi
    sleep 2
  done
  warn "packmate did not answer on :$PORT within 120s — check 'docker compose logs' in $SRC_DIR"
  return 1
}

run_configure() {
  ( cd "$SCRIPT_DIR" && python3 packmate-configure.py --port "$PORT" \
      --login "$WEB_LOGIN" --password "$WEB_PASSWORD" )
}

do_start() {
  check_deps
  write_env

  local build_args=(up -d)
  if ((BUILD)); then
    log "building from ./Packmate-master (this takes a few minutes)"
    dc build || die "build failed"
  else
    log "pulling prebuilt image"
    dc pull db || true
    dc pull packmate || warn "could not pull prebuilt image — retry with --build if offline"
  fi
  dc "${build_args[@]}" || die "docker compose up failed — check the output above"

  umask 077
  { echo "url=http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT"
    echo "port=$PORT"
    echo "login=$WEB_LOGIN"
    echo "password=$WEB_PASSWORD"
    echo "installed=$(date -Is)"; } > "$CRED_FILE"
  chmod 600 "$CRED_FILE"

  local ip; ip="$(hostname -I 2>/dev/null | awk '{print $1}')"; ip="${ip:-<host-ip>}"
  echo
  ok "Packmate is up"
  printf '    web UI   : %shttp://%s:%s%s\n' "$c_cyn" "$ip" "$PORT" "$c_off"
  printf '    login    : %s\n' "$WEB_LOGIN"
  printf '    password : %s%s%s\n' "$c_grn" "$WEB_PASSWORD" "$c_off"
  printf '    saved to : %s\n' "$CRED_FILE"
  echo

  if ((RUN_CONFIGURE)); then
    if wait_healthy && [[ -t 0 ]]; then
      run_configure
    elif [[ ! -t 0 ]]; then
      warn "no tty — skipping the interactive wizard. Run manually: cd $SCRIPT_DIR && ./packmate-setup.sh configure"
    fi
  fi
}

do_configure() {
  [[ -f "$ENV_FILE" ]] || die "no $ENV_FILE — run './packmate-setup.sh start' first"
  WEB_LOGIN="$(grep -m1 '^PACKMATE_WEB_LOGIN=' "$ENV_FILE" | cut -d= -f2-)"
  WEB_PASSWORD="$(grep -m1 '^PACKMATE_WEB_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"
  [[ -t 0 ]] || die "the wizard needs a real terminal — run this over SSH, not through the board"
  wait_healthy || true
  run_configure
}

case "$ACTION" in
  start)     do_start ;;
  configure) do_configure ;;
  stop)      dc down ;;
  restart)   dc down && dc up -d ;;
  status)    dc ps ;;
  logs)      dc logs -f ;;
  update)    dc pull && dc up -d ;;
  clean)
    read -rp "delete the packmate postgres volume with all captured traffic? [y/N] " a
    [[ "$a" =~ ^[yY]$ ]] || die "cancelled"
    dc down -v
    rm -rf "$SRC_DIR/data"
    ok "volume deleted" ;;
esac
