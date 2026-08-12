#!/usr/bin/env python3
"""Interactive wizard: ask which game services exist, register them in Packmate
(which is what turns on stream capture for their ports), and create a
"Flag OUT" / "Flag IN" regex pattern pair for each — same split the Packmate
docs and firegex-setup.md both recommend for telling the checker apart from
an exploit.

Talks to the Packmate REST API (Basic Auth, CSRF disabled) with stdlib only.
Safe to re-run: existing services/patterns are left alone.
"""
import argparse
import base64
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
ENV_FILE = SCRIPT_DIR / "Packmate-master" / ".env"
STATE_FILE = REPO_ROOT / "state" / "state.json"
ATTDEF_FILE = REPO_ROOT / "Farm" / "attdef.yml"

DEFAULT_FLAG_FORMAT = r"[A-Z0-9]{31}="
OUT_COLOR = "#ef4444"
IN_COLOR = "#3b82f6"


class Cancelled(Exception):
    pass


def section(title):
    print(f"\n== {title} ==")


def ask(label, current="", validator=None, optional=False):
    while True:
        suffix = f" [{current}]" if current not in (None, "") else ""
        raw = input(f"{label}{suffix}: ").strip()
        if raw == "":
            value = current
        elif optional and raw == "-":
            value = ""
        else:
            value = raw
        try:
            return validator(value) if validator else value
        except (TypeError, ValueError) as exc:
            print(f"Ошибка: {exc}")


def ask_yes_no(label, current=True):
    suffix = "Д/н" if current else "д/Н"
    while True:
        raw = input(f"{label} [{suffix}]: ").strip().lower()
        if not raw:
            return current
        if raw in {"y", "yes", "д", "да"}:
            return True
        if raw in {"n", "no", "н", "нет"}:
            return False
        print("Введите да или нет.")


def nonempty(value):
    value = str(value).strip()
    if not value:
        raise ValueError("значение не может быть пустым")
    return value


def positive_int(value):
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("нужно целое положительное число") from exc
    if number <= 0:
        raise ValueError("число должно быть больше нуля")
    return number


def port_value(value):
    number = positive_int(value)
    if number > 65535:
        raise ValueError("порт должен быть от 1 до 65535")
    return number


def flag_regex(value):
    value = nonempty(value)
    try:
        re.compile(value)
    except re.error as exc:
        raise ValueError(f"некорректное регулярное выражение: {exc}") from exc
    return value


def read_env(path):
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key] = value
    return env


def guess_flag_format():
    """Best-effort: reuse Farm/attdef.yml's flags.format if this checkout has one.
    Packmate is meant to be deployable on its own (just the packmate/ dir, copied
    to the vulnbox like firegex-setup.sh), so this is opportunistic, not required."""
    if not ATTDEF_FILE.exists():
        return None
    in_flags = False
    for line in ATTDEF_FILE.read_text().splitlines():
        if re.match(r"^flags:\s*$", line):
            in_flags = True
            continue
        if in_flags:
            if re.match(r"^\S", line):
                break
            m = re.match(r"^\s+format:\s*'((?:[^'\\]|\\.)*)'\s*$", line)
            if m:
                return m.group(1)
    return None


def suggest_services():
    """Best-effort: services the board (monitor.sh discover) already knows about,
    from state/state.json. Absent when packmate/ is deployed standalone."""
    if not STATE_FILE.exists():
        return []
    try:
        state = json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return []

    suggestions = []
    for name, info in (state.get("services") or {}).items():
        ports = info.get("ports") or []
        published = []
        for entry in ports:
            head = str(entry).split(":", 1)[0]
            if head.isdigit():
                published.append(int(head))
        published = sorted(set(published))
        for p in published:
            label = name if len(published) == 1 else f"{name}-{p}"
            suggestions.append((label, p))
    return suggestions


class ApiError(Exception):
    pass


class PackmateApi:
    def __init__(self, base_url, login, password):
        self.base_url = base_url.rstrip("/")
        token = base64.b64encode(f"{login}:{password}".encode()).decode()
        self.auth_header = f"Basic {token}"

    def _request(self, method, path, body=None):
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", self.auth_header)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raise ApiError(f"{method} {path} -> HTTP {exc.code}: {exc.read().decode(errors='replace')}") from exc
        except urllib.error.URLError as exc:
            raise ApiError(f"{method} {path} -> {exc.reason}") from exc

    def ping(self):
        self._request("GET", "/api/service/")

    def list_services(self):
        return self._request("GET", "/api/service/") or []

    def create_service(self, dto):
        return self._request("POST", "/api/service/", dto)

    def list_patterns(self):
        return self._request("GET", "/api/pattern/") or []

    def create_pattern(self, dto):
        return self._request("POST", "/api/pattern/", dto)


def collect_services():
    suggestions = suggest_services()
    services = []
    seen_ports = set()

    if suggestions:
        section("Уже известные сервисы")
        print("Board (monitor.sh) видит:")
        for name, port in suggestions:
            print(f"  - {name} : {port}")
        if ask_yes_no("Взять их как основу", True):
            for name, port in suggestions:
                if port in seen_ports:
                    continue
                is_http = ask_yes_no(f"  {name}:{port} — HTTP сервис", True)
                services.append({"name": name, "port": port, "http": is_http})
                seen_ports.add(port)

    section("Сервисы")
    remaining = ask("Ещё сколько сервисов добавить вручную (0 — пропустить)", 0 if services else 1, positive_int_or_zero)
    for i in range(remaining):
        while True:
            name = ask(f"Сервис {i + 1}: имя", "", nonempty)
            port = ask(f"Сервис {i + 1}: порт", "", port_value)
            if port in seen_ports:
                print(f"Ошибка: порт {port} уже добавлен")
                continue
            break
        is_http = ask_yes_no(f"  {name}:{port} — HTTP сервис", True)
        services.append({"name": name, "port": port, "http": is_http})
        seen_ports.add(port)

    return services


def positive_int_or_zero(value):
    value = str(value).strip()
    if value == "":
        return 0
    number = int(value)
    if number < 0:
        raise ValueError("число не может быть отрицательным")
    return number


def apply_config(api, services, flag_format):
    existing_services = {s["port"] for s in api.list_services()}
    existing_patterns = {p["name"] for p in api.list_patterns()}

    created_services = skipped_services = 0
    created_patterns = skipped_patterns = 0

    for svc in services:
        port, name, is_http = svc["port"], svc["name"], svc["http"]

        if port in existing_services:
            print(f"  сервис {name}:{port} уже существует — пропуск")
            skipped_services += 1
        else:
            api.create_service({
                "port": port,
                "name": name,
                "decryptTls": False,
                "http": is_http,
                "urldecodeHttpRequests": is_http,
                "mergeAdjacentPackets": is_http,
                "parseWebSockets": False,
            })
            print(f"  + сервис {name}:{port} ({'http' if is_http else 'binary'})")
            created_services += 1

        for label, direction, color in (
            ("Flag OUT", "OUTPUT", OUT_COLOR),
            ("Flag IN", "INPUT", IN_COLOR),
        ):
            pattern_name = f"{label} — {name}"
            if pattern_name in existing_patterns:
                skipped_patterns += 1
                continue
            api.create_pattern({
                "name": pattern_name,
                "value": flag_format,
                "color": color,
                "searchType": "REGEX",
                "directionType": direction,
                "actionType": "FIND",
                "serviceId": port,
            })
            existing_patterns.add(pattern_name)
            print(f"  + паттерн '{pattern_name}' ({direction})")
            created_patterns += 1

    return created_services, skipped_services, created_patterns, skipped_patterns


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=65000)
    parser.add_argument("--login")
    parser.add_argument("--password")
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit("Мастер настройки нужно запускать в обычном терминале.")

    env = read_env(ENV_FILE)
    login = args.login or env.get("PACKMATE_WEB_LOGIN")
    password = args.password or env.get("PACKMATE_WEB_PASSWORD")
    if not login or not password:
        raise SystemExit(
            f"Не найден логин/пароль. Передай --login/--password или запусти через "
            f"./packmate-setup.sh (создаёт {ENV_FILE})."
        )

    base_url = f"http://{args.host}:{args.port}"
    api = PackmateApi(base_url, login, password)

    print("Настройка Packmate: сервисы и flag in/out паттерны")
    print("Enter оставляет значение по умолчанию. Ctrl+C отменяет настройку.")
    try:
        api.ping()
    except ApiError as exc:
        raise SystemExit(f"Packmate недоступен на {base_url}: {exc}")

    try:
        services = collect_services()
        if not services:
            print("Сервисы не заданы, паттерны создавать не из чего.")
            return 0

        section("Формат флага")
        guessed = guess_flag_format()
        default_format = guessed or DEFAULT_FLAG_FORMAT
        if guessed:
            print(f"Найден формат флага в {ATTDEF_FILE}: {guessed}")
        flag_format = ask("Регулярное выражение флага", default_format, flag_regex)

        section("Проверка")
        print(f"Сервисов: {len(services)}")
        for svc in services:
            print(f"  - {svc['name']}:{svc['port']} ({'http' if svc['http'] else 'binary'})")
        print(f"Формат флага: {flag_format}")
        if not ask_yes_no("Применить в Packmate", True):
            raise Cancelled
    except (Cancelled, EOFError, KeyboardInterrupt):
        print("\nНастройка отменена.")
        return 130

    section("Применение")
    try:
        cs, ss, cp, sp = apply_config(api, services, flag_format)
    except ApiError as exc:
        raise SystemExit(f"\nОшибка Packmate API: {exc}")

    print(f"\nГотово: сервисов создано {cs}, пропущено {ss}; паттернов создано {cp}, пропущено {sp}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
