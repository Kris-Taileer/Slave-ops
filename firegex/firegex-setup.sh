#!/usr/bin/env bash
#
# firegex-setup.sh — quick deployment of Firegex (https://github.com/Pwnzer0tt1/firegex)
# on a vulnbox for Attack-Defense CTFs.
#
#   sudo ./firegex-setup.sh                       # install and start (password is generated)
#   sudo ./firegex-setup.sh -w mypassword -p 4444 # custom password and port
#   sudo ./firegex-setup.sh status|stop|restart|logs|update
#
set -Eeuo pipefail

# ----------------------------------- defaults ----------------------------------------
FIREGEX_DIR="${FIREGEX_DIR:-/opt/firegex}"
FIREGEX_REPO="${FIREGEX_REPO:-https://github.com/Pwnzer0tt1/firegex.git}"
PORT="${FIREGEX_PORT:-4444}"
PASSWORD="${FIREGEX_PASSWORD:-}"
HOST=""                 # --host: address to bind to (defaults to firegex's own config)
ALLOWED_IPS=""          # --allowed-ips: CIDRs allowed to reach the web UI
MODE="prebuilt"         # prebuilt | build | standalone
INSTALL_DEPS=1
ACTION="start"
CRED_FILE=""

# ----------------------------------- helpers -----------------------------------------
c_red=$'\033[1;31m'; c_grn=$'\033[1;32m'; c_ylw=$'\033[1;33m'; c_cyn=$'\033[1;36m'; c_off=$'\033[0m'
log()  { printf '%s[*]%s %s\n' "$c_cyn" "$c_off" "$*"; }
ok()   { printf '%s[+]%s %s\n' "$c_grn" "$c_off" "$*"; }
warn() { printf '%s[!]%s %s\n' "$c_ylw" "$c_off" "$*" >&2; }
die()  { printf '%s[-]%s %s\n' "$c_red" "$c_off" "$*" >&2; exit 1; }
has()  { command -v "$1" >/dev/null 2>&1; }

usage() {
  cat <<'EOF'
firegex-setup.sh [action] [options]

Actions:
  start (default)        install deps, clone/update the repo, start the service
  stop                   stop firegex
  restart                restart it
  status                 show service state
  logs                   show logs
  update                 git pull + restart
  clean                  stop and delete the volume with all settings (!)

Options:
  -p, --port N           web interface port (default 4444)
  -w, --password PSW     password (generated and printed at the end if omitted)
      --host IP          address to bind to
      --allowed-ips CIDR CIDR list allowed to reach the web UI
      --build            build the image from source instead of pulling (slower)
      --standalone       run without docker (rootless / no docker available)
  -d, --dir PATH         install location (default /opt/firegex)
      --no-deps          do not install dependencies
  -h, --help             this help

Environment variables: FIREGEX_DIR, FIREGEX_PORT, FIREGEX_PASSWORD, FIREGEX_REPO
EOF
}

# --------------------------------- argument parsing ----------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    start|stop|restart|status|logs|update|clean) ACTION="$1"; shift ;;
    -p|--port)        PORT="${2:?port value required}"; shift 2 ;;
    -w|--password)    PASSWORD="${2:?password required}"; shift 2 ;;
    --host)           HOST="${2:?address required}"; shift 2 ;;
    --allowed-ips)    ALLOWED_IPS="${2:?CIDR list required}"; shift 2 ;;
    --build)          MODE="build"; shift ;;
    --standalone)     MODE="standalone"; shift ;;
    -d|--dir)         FIREGEX_DIR="${2:?path required}"; shift 2 ;;
    --no-deps)        INSTALL_DEPS=0; shift ;;
    -h|--help)        usage; exit 0 ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
done
CRED_FILE="$FIREGEX_DIR/.firegex-credentials"

# ---------------------------------- preflight checks ---------------------------------
[[ "$(uname -s)" == "Linux" ]] || die "Firegex only runs on Linux (needs nftables/NFQUEUE). Run this on the vulnbox."
[[ $EUID -eq 0 ]] || die "root required: sudo $0 $*"

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
    log "installing: ${missing[*]}"
    pkg_install "${missing[@]}" || die "could not install ${missing[*]} — install them manually and rerun with --no-deps"
  fi

  # the regex filter needs these nftables kernel modules (NFQUEUE)
  modprobe nfnetlink_queue 2>/dev/null || true
  modprobe nft_queue       2>/dev/null || true

  if [[ "$MODE" == "standalone" ]]; then
    ok "standalone mode — docker not needed"
    return
  fi

  if ! has docker; then
    log "docker not found, installing via get.docker.com"
    curl -fsSL https://get.docker.com | sh || die "docker installation failed; try --standalone"
  fi
  systemctl enable --now docker >/dev/null 2>&1 || true
  docker info >/dev/null 2>&1 || die "docker is installed but the daemon is not responding (systemctl start docker)"

  if ! docker compose version >/dev/null 2>&1 && ! has docker-compose; then
    log "installing the docker compose plugin"
    pkg_install docker-compose-plugin || pkg_install docker-compose \
      || die "no docker compose — install the plugin manually or use --standalone"
  fi
  ok "docker ready"
}

fetch_repo() {
  if [[ -d "$FIREGEX_DIR/.git" ]]; then
    log "repo already present in $FIREGEX_DIR, updating"
    git -C "$FIREGEX_DIR" pull --ff-only || warn "git pull failed, continuing with the current version"
  else
    log "cloning firegex into $FIREGEX_DIR"
    mkdir -p "$(dirname "$FIREGEX_DIR")"
    git clone --depth 1 "$FIREGEX_REPO" "$FIREGEX_DIR" || die "clone failed"
  fi
  [[ -f "$FIREGEX_DIR/run.py" ]] || die "no run.py in $FIREGEX_DIR — this does not look like the firegex repository"
}

gen_password() {
  if has openssl; then openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 20
  else tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20; fi
}

# always run run.py from the repo directory — it keeps .firegex-conf.json there
fg() { ( cd "$FIREGEX_DIR" && python3 run.py "$@" ); }

# mode flag as an array: --prebuilt / --standalone / nothing (build from source)
MODE_FLAGS=()
case "$MODE" in
  prebuilt)   MODE_FLAGS=(--prebuilt) ;;
  standalone) MODE_FLAGS=(--standalone) ;;
esac
# stop/status/restart need --standalone too, but never --prebuilt
SFLAG=()
[[ "$MODE" == standalone ]] && SFLAG=(--standalone)

do_start() {
  ((INSTALL_DEPS)) && install_deps
  fetch_repo

  if [[ -z "$PASSWORD" ]]; then
    PASSWORD="$(gen_password)"
    warn "no password given — generated a new one"
  fi

  local args=(start ${MODE_FLAGS[@]+"${MODE_FLAGS[@]}"} --port "$PORT" --startup-psw "$PASSWORD")
  [[ -n "$HOST"        ]] && args+=(--host "$HOST")
  [[ -n "$ALLOWED_IPS" ]] && args+=(--allowed-ips "$ALLOWED_IPS")

  log "running: python3 run.py ${args[*]//"$PASSWORD"/******}"
  fg "${args[@]}" || die "firegex failed to start — check the output above"

  umask 077
  { echo "url=http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT"
    echo "port=$PORT"
    echo "password=$PASSWORD"
    echo "installed=$(date -Is)"; } > "$CRED_FILE"
  chmod 600 "$CRED_FILE"

  local ip; ip="$(hostname -I 2>/dev/null | awk '{print $1}')"; ip="${ip:-<vulnbox-ip>}"
  echo
  ok "Firegex is up"
  printf '    web UI   : %shttp://%s:%s%s\n' "$c_cyn" "$ip" "$PORT" "$c_off"
  printf '    password : %s%s%s\n' "$c_grn" "$PASSWORD" "$c_off"
  printf '    saved to : %s\n' "$CRED_FILE"
  echo
  cat <<EOF
Next, in the UI:
  1. Regex filter  — add a service (port, proto, ipv4/6) and enable it → traffic goes through NFQUEUE.
  2. Write rules as base64 regexes; start by blacklisting the exploit's flag format, not all input.
  3. Firewall      — basic allow/deny, a ufw equivalent, to close the ports you do not need.
  4. Port hijack   — redirect a service port to your own proxy.
Make sure $PORT is only reachable by your team (--allowed-ips CIDR), or opponents will take the UI.
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
    read -rp "delete the firegex volume with all settings? [y/N] " a
    [[ "$a" =~ ^[yY]$ ]] || die "cancelled"
    fg stop --clear ${SFLAG[@]+"${SFLAG[@]}"}
    ok "volume deleted" ;;
esac
