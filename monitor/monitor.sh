#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$SCRIPT_DIR/monitor.conf"
if [ -f "$CONFIG_FILE" ]; then
  source "$CONFIG_FILE"
fi
SERVICES_DIR="${MONITOR_SERVICES_DIR:-${SERVICES_DIR:-$REPO_ROOT/services}}"
WEB_DIR="$SCRIPT_DIR/web"
STATE_DIR="$SCRIPT_DIR/state"
STATE_FILE="$STATE_DIR/state.json"
LOG_FILE="$STATE_DIR/events.log"
LOG_LOCK="$STATE_DIR/events.log.lock"
STATE_LOCK="$STATE_DIR/state.lock"
WATCH_LOCK="$STATE_DIR/watch.lock"
TOKEN_FILE="$STATE_DIR/token"
BACKUP_DIR="$STATE_DIR/backups"
HEALTH_INTERVAL="${MONITOR_INTERVAL:-60}"

SCAN_TIMEOUT="${MONITOR_SCAN_TIMEOUT:-180}"

SCAN_EXCLUDE_DEFAULT=(
  /proc /sys /dev /run
  /var/lib/docker /var/lib/containerd /var/lib/snapd /snap
  '*/node_modules' '*/go/pkg/mod' '*/.cargo/registry' '*/__pycache__' '*/.local/share/Trash'
)

COMPOSE_CANDIDATES=(docker-compose.yaml docker-compose.yml compose.yaml compose.yml)

WEAK_CREDENTIALS_JSON='["password","admin","administrator","root","toor","123456","12345678","123456789","1234","12345","qwerty","letmein","changeme","change_me","changethis","test","testtest","guest","secret","admin123","pass111","root123","default","demo","temp","password1","password123","mysecretpassword","postgres","mysql","redis","mongo","rootpassword","insecure","foobar","111111","000000","1q2w3e","welcome"]'
read -r -d '' FINDINGS_FILTER <<'JQEOF' || true
def weak_creds:
  (.services // {}) | to_entries[] | .key as $svc |
  ((.value.environment // {}) | to_entries[] |
    select(.key | test("PASS|PWD|SECRET|TOKEN|APIKEY|API_KEY"; "i")) |
    (.value // "") as $v |
    if ($v == "") then
      {severity:"high", code:"empty_credential", service:$svc,
       message: ($svc + ": " + .key + " пустой — аутентификация фактически отключена")}
    elif ($weak | index($v | ascii_downcase)) then
      {severity:"high", code:"weak_default_credential", service:$svc,
       message: ($svc + ": " + .key + " = \"" + $v + "\" — дефолтное/словарное значение")}
    else empty end
  );

def db_exposed:
  (.services // {}) | to_entries[] | .key as $svc | .value as $sv |
  select(($sv.image // "") | test("postgres|mysql|mariadb|mongo|redis|elasticsearch|memcached|rabbitmq|cassandra|couchdb|cockroach"; "i")) |
  select(($sv.ports // []) | any(.host_ip == null or (.host_ip != "127.0.0.1" and .host_ip != "::1"))) |
  {severity:"high", code:"db_exposed", service:$svc,
   message: ($svc + ": БД (" + $sv.image + ") опубликована на всех интерфейсах — доступна не только из docker-сети")};

def privileged_container:
  (.services // {}) | to_entries[] | .key as $svc |
  select(.value.privileged == true) |
  {severity:"medium", code:"privileged_container", service:$svc,
   message: ($svc + ": контейнер запущен privileged")};

def host_network:
  (.services // {}) | to_entries[] | .key as $svc |
  select(.value.network_mode == "host") |
  {severity:"medium", code:"host_network", service:$svc,
   message: ($svc + ": network_mode: host — контейнер использует сетевой стек хоста")};

def docker_socket_mounted:
  (.services // {}) | to_entries[] | .key as $svc | .value as $sv |
  select(($sv.volumes // []) | any(.source == "/var/run/docker.sock")) |
  {severity:"high", code:"docker_socket_mounted", service:$svc,
   message: ($svc + ": примонтирован docker.sock — контейнер имеет фактический root на хосте")};

[weak_creds, db_exposed, privileged_container, host_network, docker_socket_mounted] | flatten
JQEOF

security_scan() {
  local cfg_json="$1"
  local findings
  findings="$(printf '%s' "$cfg_json" | jq -c --argjson weak "$WEAK_CREDENTIALS_JSON" "$FINDINGS_FILTER" 2>/dev/null)"
  if [ -z "$findings" ] || [ "$findings" = "null" ]; then
    findings="[]"
  fi
  printf '%s' "$findings"
}

log() {
  local level="$1" service="$2" msg="$3" ts
  ts="$(date -Iseconds)"
  msg="${msg//$'\n'/ }"
  msg="${msg//$'\t'/ }"
  mkdir -p "$STATE_DIR"
  {
    flock -w 5 9 || true
    printf '%s\t%s\t%s\t%s\n' "$ts" "$level" "$service" "$msg" >> "$LOG_FILE"
  } 9>>"$LOG_LOCK"
  echo "[$ts] [$level] [$service] $msg" >&2
}

check_deps() {
  local missing=()
  command -v docker >/dev/null 2>&1 || missing+=(docker)
  command -v jq >/dev/null 2>&1 || missing+=(jq)
  command -v flock >/dev/null 2>&1 || missing+=(flock)
  docker compose version >/dev/null 2>&1 || missing+=("docker compose plugin")
  if [ ${#missing[@]} -gt 0 ]; then
    echo "Missing dependencies: ${missing[*]}" >&2
    exit 1
  fi
}

state_init() {
  mkdir -p "$STATE_DIR" "$BACKUP_DIR"
  if [ ! -f "$STATE_FILE" ]; then
    echo '{"generated_at":null,"services":{}}' > "$STATE_FILE"
  fi
}

init_all() {
  state_init
  mkdir -p "$WEB_DIR"
  if [ ! -f "$TOKEN_FILE" ]; then
    ( umask 077; head -c 24 /dev/urandom | base64 | tr -d '=+/\n' > "$TOKEN_FILE" )
    log info monitor "generated new access token"
  fi
  chmod 600 "$TOKEN_FILE" 2>/dev/null || true
}

jq_update() {
  local filter="$1"; shift
  local tmp
  tmp="$(mktemp "$STATE_DIR/state.json.XXXXXX")" || return 1
  if jq "$filter" "$@" "$STATE_FILE" > "$tmp" 2>"$tmp.err"; then
    mv "$tmp" "$STATE_FILE"
    rm -f "$tmp.err"
  else
    log error monitor "jq_update failed: $(cat "$tmp.err" 2>/dev/null)"
    rm -f "$tmp" "$tmp.err"
    return 1
  fi
}

with_state_lock() {
  (
    flock -w 10 8 || { echo "failed to acquire state lock" >&2; exit 1; }
    "$@"
  ) 8>>"$STATE_LOCK"
}

find_compose_file() {
  local dir="$1" cand
  for cand in "${COMPOSE_CANDIDATES[@]}"; do
    if [ -f "$dir/$cand" ]; then
      echo "$dir/$cand"
      return 0
    fi
  done
  return 1
}

get_compose_file() {
  local name="$1"
  jq -r --arg n "$name" '.services[$n].compose_file // empty' "$STATE_FILE" 2>/dev/null
}

service_exists() {
  local name="$1"
  [ "$(jq -r --arg n "$name" '.services | has($n)' "$STATE_FILE" 2>/dev/null)" = "true" ]
}

register_service_dir() {
  local id="$1" dir="$2" source_tag="$3"
  local compose_file
  local errfile
  errfile="$(mktemp "$STATE_DIR/.discover_err.XXXXXX")"

  if ! compose_file="$(find_compose_file "$dir")"; then
    with_state_lock jq_update \
      '.services[$n] = ((.services[$n] // {}) + {name:$n, path:$p, compose_file:null, ports:[], source:$src}) | .services[$n].status = "error" | .services[$n].last_error = $e | .services[$n].last_check = $t | .services[$n].containers = (.services[$n].containers // []) | .services[$n].findings = []' \
      --arg n "$id" --arg p "$dir" --arg src "$source_tag" --arg e "no docker-compose file found" --arg t "$(date -Iseconds)"
    log warn "$id" "no docker-compose file found in $dir"
    rm -f "$errfile"
    return 1
  fi

  local cfg_err rc cfg
  cfg="$(docker compose -f "$compose_file" config --format json 2>"$errfile")"
  rc=$?
  cfg_err="$(cat "$errfile" 2>/dev/null)"
  rm -f "$errfile"

  if [ $rc -ne 0 ]; then
    with_state_lock jq_update \
      '.services[$n] = ((.services[$n] // {}) + {name:$n, path:$p, compose_file:$f, ports:[], source:$src}) | .services[$n].status = "error" | .services[$n].last_error = $e | .services[$n].last_check = $t | .services[$n].containers = (.services[$n].containers // []) | .services[$n].findings = []' \
      --arg n "$id" --arg p "$dir" --arg f "$compose_file" --arg src "$source_tag" --arg e "$cfg_err" --arg t "$(date -Iseconds)"
    log error "$id" "invalid compose file: $cfg_err"
    return 1
  fi

  local ports findings
  ports="$(echo "$cfg" | jq -c '[.services[].ports[]? | ((.published|tostring) + ":" + (.target|tostring))] | unique' 2>/dev/null)"
  [ -z "$ports" ] && ports="[]"
  findings="$(security_scan "$cfg")"

  local prev_findings_count new_findings_count
  prev_findings_count="$(jq -r --arg n "$id" '.services[$n].findings // [] | length' "$STATE_FILE" 2>/dev/null)"
  new_findings_count="$(echo "$findings" | jq 'length' 2>/dev/null)"
  if [ "${new_findings_count:-0}" -gt 0 ] && [ "${new_findings_count:-0}" != "${prev_findings_count:-0}" ]; then
    log warn "$id" "security scan: ${new_findings_count} finding(s)"
  fi

  with_state_lock jq_update \
    '.services[$n] = ((.services[$n] // {}) + {name:$n, path:$p, compose_file:$f, ports:$ports, last_error:null, source:$src, findings:$findings}) | .services[$n].status = (.services[$n].status // "unknown") | .services[$n].containers = (.services[$n].containers // []) | .services[$n].last_check = $t' \
    --arg n "$id" --arg p "$dir" --arg f "$compose_file" --argjson ports "$ports" --arg src "$source_tag" --argjson findings "$findings" --arg t "$(date -Iseconds)"
  return 0
}

discover() {
  state_init
  local found_root=0 found_services=0
  local dir name
  shopt -s nullglob

  for dir in "$REPO_ROOT"/*/; do
    [ -d "$dir" ] || continue
    dir="${dir%/}"
    [ "$dir" = "$SCRIPT_DIR" ] && continue
    [ "$dir" = "$SERVICES_DIR" ] && continue
    find_compose_file "$dir" >/dev/null 2>&1 || continue
    name="$(basename "$dir")"
    found_root=$((found_root + 1))
    register_service_dir "$name" "$dir" "root_dir" || true
  done

  if [ -d "$SERVICES_DIR" ]; then
    for dir in "$SERVICES_DIR"/*/; do
      [ -d "$dir" ] || continue
      dir="${dir%/}"
      name="$(basename "$dir")"
      found_services=$((found_services + 1))
      register_service_dir "$name" "$dir" "services_dir" || true
    done
  else
    log warn monitor "services directory does not exist: $SERVICES_DIR (check MONITOR_SERVICES_DIR / monitor.conf)"
  fi

  local existing svc path
  existing="$(jq -r '.services | to_entries[] | select(.value.source=="root_dir" or .value.source=="services_dir") | "\(.key)\t\(.value.path)"' "$STATE_FILE" 2>/dev/null)"
  while IFS=$'\t' read -r svc path; do
    [ -z "$svc" ] && continue
    if [ ! -d "$path" ]; then
      with_state_lock jq_update 'del(.services[$n])' --arg n "$svc"
      log warn "$svc" "service directory removed, dropped from registry"
    fi
  done <<< "$existing"

  with_state_lock jq_update '.generated_at = $t' --arg t "$(date -Iseconds)"
  log info monitor "local discovery complete: nearby=$found_root services_dir=$found_services"
}

full_scan() {
  state_init
  local start_ts; start_ts="$(date +%s)"
  log info monitor "full-disk scan started (timeout ${SCAN_TIMEOUT}s)"

  local -a excludes=("${SCAN_EXCLUDE_DEFAULT[@]}" "$STATE_DIR")
  if [ -n "${MONITOR_SCAN_EXCLUDE:-}" ]; then
    local extra
    IFS=':' read -ra extra <<< "$MONITOR_SCAN_EXCLUDE"
    excludes+=("${extra[@]}")
  fi
  local -a prune_args=()
  local p
  for p in "${excludes[@]}"; do
    prune_args+=(-path "$p" -o)
  done
  unset 'prune_args[${#prune_args[@]}-1]'

  local results_file
  results_file="$(mktemp "$STATE_DIR/.fullscan.XXXXXX")"
  timeout "$SCAN_TIMEOUT" find / \( "${prune_args[@]}" \) -prune -o -type f \
    \( -iname 'docker-compose.yaml' -o -iname 'docker-compose.yml' -o -iname 'compose.yaml' -o -iname 'compose.yml' \) \
    -print 2>/dev/null > "$results_file"
  if [ $? -eq 124 ]; then
    log warn monitor "full-disk scan hit the ${SCAN_TIMEOUT}s timeout, results may be incomplete (tune MONITOR_SCAN_TIMEOUT)"
  fi

  local count=0 f dir id hash
  local -a found_ids=()
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    dir="$(dirname "$f")"
    case "$dir" in
      "$SERVICES_DIR"|"$SERVICES_DIR"/*) continue ;;
    esac
    count=$((count + 1))
    hash="$(printf '%s' "$dir" | md5sum | cut -c1-8)"
    id="$(basename "$dir")__${hash}"
    found_ids+=("$id")
    register_service_dir "$id" "$dir" "scanned" || true
  done < "$results_file"
  rm -f "$results_file"

  local existing_scanned svc still_present fid
  existing_scanned="$(jq -r '.services | to_entries[] | select(.value.source=="scanned") | .key' "$STATE_FILE" 2>/dev/null)"
  while IFS= read -r svc; do
    [ -z "$svc" ] && continue
    still_present=false
    for fid in "${found_ids[@]:-}"; do
      [ "$fid" = "$svc" ] && still_present=true && break
    done
    if [ "$still_present" = false ]; then
      with_state_lock jq_update 'del(.services[$n])' --arg n "$svc"
      log warn "$svc" "no longer found in full-disk scan, dropped from registry"
    fi
  done <<< "$existing_scanned"

  with_state_lock jq_update '.generated_at = $t | .last_full_scan = $t' --arg t "$(date -Iseconds)"
  local elapsed=$(( $(date +%s) - start_ts ))
  log info monitor "full-disk scan complete: ${count} compose file(s) outside services/ in ${elapsed}s"
}

port_reachable() {
  local port="$1"
  timeout 3 bash -c "exec 3<>/dev/tcp/127.0.0.1/$port" 2>/dev/null
  local rc=$?
  exec 3>&- 2>/dev/null || true
  return $rc
}

health_check_service() {
  local name="$1"
  local compose_file
  compose_file="$(get_compose_file "$name")"
  if [ -z "$compose_file" ] || [ "$compose_file" = "null" ]; then
    return 0
  fi

  local ps_json rc err t
  ps_json="$(docker compose -f "$compose_file" ps --all --format json 2>"$STATE_DIR/.hc_err")"
  rc=$?
  err="$(cat "$STATE_DIR/.hc_err" 2>/dev/null)"
  rm -f "$STATE_DIR/.hc_err"
  t="$(date -Iseconds)"

  if [ $rc -ne 0 ]; then
    with_state_lock jq_update '.services[$n].status="error" | .services[$n].last_error=$e | .services[$n].last_check=$t' \
      --arg n "$name" --arg e "docker compose ps failed: $err" --arg t "$t"
    log error "$name" "docker compose ps failed: $err"
    return 0
  fi

  local containers_json total running
  containers_json="$(printf '%s\n' "$ps_json" | jq -cs 'map(select(. != null))')"
  total="$(echo "$containers_json" | jq 'length')"

  if [ "$total" -eq 0 ]; then
    with_state_lock jq_update '.services[$n].status="down" | .services[$n].containers=[] | .services[$n].last_error=null | .services[$n].last_check=$t' \
      --arg n "$name" --arg t "$t"
    return 0
  fi

  running="$(echo "$containers_json" | jq '[.[] | select(.State=="running")] | length')"
  local names_list status err_detail
  names_list="$(echo "$containers_json" | jq -c '[.[].Name]')"
  err_detail=""

  if [ "$running" -eq "$total" ]; then
    status="up"
  else
    status="error"
    err_detail="$(echo "$containers_json" | jq -r '[.[] | select(.State!="running") | "\(.Name): \(.State) exit=\(.ExitCode)"] | join("; ")')"
    for bad in $(echo "$containers_json" | jq -r '.[] | select(.State!="running") | .Name'); do
      local tail_log
      tail_log="$(docker logs --tail 5 "$bad" 2>&1 | tr '\n' ' ')"
      log error "$name" "container $bad not running: ${tail_log:0:300}"
    done
  fi

  if [ -n "$err_detail" ]; then
    with_state_lock jq_update '.services[$n].status=$s | .services[$n].containers=$c | .services[$n].last_error=$e | .services[$n].last_check=$t' \
      --arg n "$name" --arg s "$status" --argjson c "$names_list" --arg e "$err_detail" --arg t "$t"
  else
    with_state_lock jq_update '.services[$n].status=$s | .services[$n].containers=$c | .services[$n].last_error=null | .services[$n].last_check=$t' \
      --arg n "$name" --arg s "$status" --argjson c "$names_list" --arg t "$t"
  fi

  if [ "$status" = "up" ]; then
    local ports p unreachable=""
    ports="$(jq -r --arg n "$name" '.services[$n].ports[]?' "$STATE_FILE" | cut -d: -f1)"
    for p in $ports; do
      [ -z "$p" ] && continue
      if ! port_reachable "$p"; then
        unreachable="$unreachable $p"
      fi
    done
    if [ -n "$unreachable" ]; then
      with_state_lock jq_update '.services[$n].status="error" | .services[$n].last_error=$e | .services[$n].last_check=$t' \
        --arg n "$name" --arg e "containers up but port(s)$unreachable not accepting TCP connections" --arg t "$(date -Iseconds)"
      log error "$name" "containers up but port(s)$unreachable not accepting TCP connections"
    fi
  fi
}

service_action() {
  local name="$1" action="$2"
  local compose_file
  compose_file="$(get_compose_file "$name")"
  if [ -z "$compose_file" ] || [ "$compose_file" = "null" ]; then
    log error "$name" "cannot $action: service unknown or has no compose file"
    return 1
  fi

  local out rc t
  t="$(date -Iseconds)"
  case "$action" in
    start)
      out="$(docker compose -f "$compose_file" up -d 2>&1)"; rc=$?
      ;;
    stop)
      out="$(docker compose -f "$compose_file" down 2>&1)"; rc=$?
      ;;
    restart)
      out="$(docker compose -f "$compose_file" down 2>&1)"; rc=$?
      if [ $rc -eq 0 ]; then
        local out2
        out2="$(docker compose -f "$compose_file" up -d 2>&1)"; rc=$?
        out="$out"$'\n'"$out2"
      fi
      ;;
    *)
      log error "$name" "unknown action $action"
      return 1
      ;;
  esac

  if [ $rc -eq 0 ]; then
    if [ "$action" = "stop" ]; then
      with_state_lock jq_update '.services[$n].status="down" | .services[$n].last_stopped=$t | .services[$n].last_error=null' --arg n "$name" --arg t "$t"
    else
      with_state_lock jq_update '.services[$n].status="up" | .services[$n].last_started=$t | .services[$n].last_error=null' --arg n "$name" --arg t "$t"
    fi
    log info "$name" "$action succeeded"
  else
    with_state_lock jq_update '.services[$n].status="error" | .services[$n].last_error=$e | .services[$n].last_check=$t' --arg n "$name" --arg e "$out" --arg t "$t"
    log error "$name" "$action failed: $out"
  fi

  health_check_service "$name" || true
  echo "$out"
  return $rc
}

restart_all() {
  local names n
  names="$(jq -r '.services | keys[]' "$STATE_FILE" 2>/dev/null)"
  log info monitor "restart-all requested"
  while IFS= read -r n; do
    [ -z "$n" ] && continue
    service_action "$n" restart || true
  done <<< "$names"
  log info monitor "restart-all complete"
}

validate_compose() {
  local path="$1"
  local err
  err="$(docker compose -f "$path" config 2>&1 >/dev/null)"
  local rc=$?
  if [ $rc -ne 0 ]; then
    echo "$err"
    return 1
  fi
  echo "ok"
  return 0
}

save_compose() {
  local name="$1" tmpfile="$2" apply="${3:-}"
  local compose_file
  compose_file="$(get_compose_file "$name")"
  if [ -z "$compose_file" ] || [ "$compose_file" = "null" ]; then
    echo "unknown service: $name" >&2
    return 1
  fi

  local verr
  if ! verr="$(validate_compose "$tmpfile")"; then
    log error "$name" "compose validation failed, not saved: $verr"
    echo "validation failed: $verr"
    return 1
  fi

  mkdir -p "$BACKUP_DIR"
  local ts; ts="$(date +%Y%m%d%H%M%S)"
  cp "$compose_file" "$BACKUP_DIR/${name}-${ts}.yaml" 2>/dev/null || true
  cp "$tmpfile" "$compose_file"
  log info "$name" "compose file saved (backup: ${name}-${ts}.yaml)"
  echo "saved"

  if [ "$apply" = "--apply" ]; then
    log info "$name" "apply requested, restarting"
    ( discover >/dev/null 2>&1; service_action "$name" restart >/dev/null 2>&1 & )
  fi
  return 0
}

watch_loop() {
  mkdir -p "$STATE_DIR"
  exec 200>>"$WATCH_LOCK"
  if ! flock -n 200; then
    log warn monitor "watch loop already running elsewhere (lock held), exiting this instance"
    return 0
  fi
  log info monitor "watch loop started (pid $$, interval ${HEALTH_INTERVAL}s)"
  while true; do
    discover || log error monitor "discover cycle failed"
    local names n
    names="$(jq -r '.services | keys[]' "$STATE_FILE" 2>/dev/null)"
    while IFS= read -r n; do
      [ -z "$n" ] && continue
      health_check_service "$n" || log error "$n" "health check crashed"
    done <<< "$names"
    sleep "$HEALTH_INTERVAL"
  done
}

usage() {
  cat <<EOF
Usage: $(basename "$0") <command> [args]

Commands:
  up                        Discover services, start watch loop + web board (default)
  discover                  Rescan the repo root and services/ once (fast, local only)
  full-scan                 On-demand whole-machine sweep for compose files anywhere
                             else on disk (slow — see MONITOR_SCAN_TIMEOUT / MONITOR_SCAN_EXCLUDE).
                             Never runs automatically — CLI only, or the board's
                             Full scan button.
  status                    Print current state.json
  start <name>              docker compose up -d for a service
  stop <name>               docker compose down for a service
  restart <name>            docker compose down && up -d for a service
  restart-all                Restart every known service
  validate <compose-file>   Validate a compose file (docker compose config)
  save-compose <name> <file> [--apply]
                             Validate + write a new compose file for <name>,
                             backing up the previous version. --apply restarts
                             the service afterwards.
  watch                     Run the health-check loop in the foreground

Env / monitor.conf overrides:
  MONITOR_SERVICES_DIR, SERVICES_DIR   services/ location (see top of script)
  MONITOR_SCAN_TIMEOUT (default 180)   full-disk scan timeout, seconds
  MONITOR_SCAN_EXCLUDE                 extra colon-separated paths to prune
EOF
}

main() {
  check_deps
  init_all

  local cmd="${1:-up}"
  case "$cmd" in
    discover)
      discover
      ;;
    full-scan)
      discover
      full_scan
      ;;
    status)
      cat "$STATE_FILE"
      ;;
    start)
      service_action "$2" start
      ;;
    stop)
      service_action "$2" stop
      ;;
    restart)
      service_action "$2" restart
      ;;
    restart-all)
      restart_all
      ;;
    validate)
      validate_compose "$2"
      ;;
    save-compose)
      save_compose "$2" "$3" "${4:-}"
      ;;
    watch)
      watch_loop
      ;;
    up)
      discover
      watch_loop &
      WATCH_PID=$!
      python3 "$SCRIPT_DIR/backend.py" --root "$REPO_ROOT" --monitor-dir "$SCRIPT_DIR" --port "${MONITOR_PORT:-8080}" --bind "${MONITOR_BIND:-0.0.0.0}" &
      BACKEND_PID=$!
      SHUTTING_DOWN=0
      shutdown_handler() {
        [ "$SHUTTING_DOWN" = 1 ] && return
        SHUTTING_DOWN=1
        log info monitor "shutting down"
        kill -TERM "$WATCH_PID" "$BACKEND_PID" 2>/dev/null
        sleep 0.3
        kill -KILL "$WATCH_PID" "$BACKEND_PID" 2>/dev/null
        wait 2>/dev/null
      }
      trap shutdown_handler EXIT INT TERM
      echo "Vulnbox Service Manager running."
      echo "Board:  http://${MONITOR_BIND:-0.0.0.0}:${MONITOR_PORT:-8080}/"
      echo "Token:  $(cat "$TOKEN_FILE")"
      wait "$BACKEND_PID"
      ;;
    -h|--help|help)
      usage
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"
