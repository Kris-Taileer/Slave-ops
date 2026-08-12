#!/usr/bin/env python3
import getpass
import ipaddress
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from apply_config import load_env, parse_simple_yaml, validate_protocol  # noqa: E402


PORT_FIELDS = (
    ("s4dfarm", "S4DFarm HTTP", 5137),
    ("https_s4dfarm", "S4DFarm HTTPS", 5443),
    ("cacheproxy", "Cache proxy", 8888),
    ("external_redis", "Внешний Redis", 6378),
    ("neo_grpc", "Neo gRPC", 5005),
    ("neo_webui", "Neo Web UI HTTP", 8090),
    ("https_neo_webui", "Neo Web UI HTTPS", 8443),
    ("victoria", "VictoriaMetrics", 8428),
)
TCP_PROTOCOLS = {"ctfcup_tcp", "faust", "ructf_tcp"}
HTTP_PROTOCOLS = {"ructf_http"}


class Cancelled(Exception):
    pass


def section(title):
    print(f"\n== {title} ==")


def ask(label, current="", validator=None, secret=False, optional=False):
    while True:
        if secret:
            suffix = " [уже задан]" if current else " [не задан]"
            raw = getpass.getpass(f"{label}{suffix}: ")
        else:
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
    if "\n" in value or "\r" in value:
        raise ValueError("перенос строки недопустим")
    return value


def project_name(value):
    value = nonempty(value)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value):
        raise ValueError(
            "используй строчные латинские буквы, цифры, дефис или подчёркивание"
        )
    return value


def positive_int(value):
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("нужно целое положительное число") from exc
    if number <= 0:
        raise ValueError("число должно быть больше нуля")
    return number


def port(value):
    number = positive_int(value)
    if number > 65535:
        raise ValueError("порт должен быть от 1 до 65535")
    return number


def host(value):
    value = nonempty(value)
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        pass

    if len(value) > 253 or not re.fullmatch(
        r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?",
        value,
    ):
        raise ValueError("нужен корректный IP-адрес или домен")
    return value


def timezone(value):
    value = nonempty(value)
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("часовой пояс не найден, пример: Europe/Moscow") from exc
    return value


def flag_regex(value):
    value = nonempty(value)
    try:
        re.compile(value)
    except re.error as exc:
        raise ValueError(f"некорректное регулярное выражение: {exc}") from exc
    return value


def duration(value):
    value = nonempty(value)
    if not re.fullmatch(r"\d+(?:ms|s|m|h)", value):
        raise ValueError("формат времени: 500ms, 5s, 2m или 1h")
    return value


def endpoint_url(value):
    value = nonempty(value)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("нужен полный URL, например https://checksystem/api/flags")
    return value


def optional_url(value):
    if value == "":
        return ""
    return endpoint_url(value)


def team_name(value):
    value = nonempty(value)
    if ":" in value:
        raise ValueError("имя команды не должно содержать двоеточие")
    return value


def protocol(value):
    value = nonempty(value)
    try:
        validate_protocol(value)
    except SystemExit as exc:
        raise ValueError(str(exc)) from exc
    return value


def yaml_scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if value is None:
        return "''"
    return "'" + str(value).replace("'", "''") + "'"


def dump_simple_yaml(cfg, indent=0):
    lines = []
    prefix = " " * indent
    for key, value in cfg.items():
        key = str(key)
        if not key or ":" in key or "\n" in key or "\r" in key:
            raise ValueError(f"некорректное имя поля: {key!r}")
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            lines.extend(dump_simple_yaml(value, indent + 2).splitlines())
        else:
            lines.append(f"{prefix}{key}: {yaml_scalar(value)}")
    return "\n".join(lines)


def write_atomic(path, text, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_secrets(path, values):
    clean = {}
    for key, value in values.items():
        value = str(value).replace("\n", "").replace("\r", "")
        if value:
            clean[key] = value
    lines = [
        "# Локальные секреты фермы. Файл исключен из Git.",
        "# Создан scripts/configure.py.",
    ]
    lines.extend(f"{key}={value}" for key, value in sorted(clean.items()))
    write_atomic(path, "\n".join(lines) + "\n", 0o600)


def ensure_maps(cfg):
    for key in ("server", "ports", "teams", "flags", "submitter", "checksystem", "neo"):
        value = cfg.setdefault(key, {})
        if not isinstance(value, dict):
            raise SystemExit(f"{key} должен быть разделом конфигурации")


def configure(cfg, secrets):
    ensure_maps(cfg)
    server = cfg["server"]
    ports = cfg["ports"]
    flags = cfg["flags"]
    submitter = cfg["submitter"]
    checksystem = cfg["checksystem"]
    neo = cfg["neo"]

    section("Сервер")
    cfg["project"] = ask(
        "Имя Docker-проекта",
        cfg.get("project", "vibe_cat_but_sad_farm"),
        project_name,
    )
    server["public_ip"] = ask(
        "Публичный IP или домен", server.get("public_ip", "127.0.0.1"), host
    )
    server["timezone"] = ask(
        "Часовой пояс", server.get("timezone", "Europe/Moscow"), timezone
    )

    section("Порты")
    used_ports = {}
    for key, label, default in PORT_FIELDS:
        while True:
            selected_port = ask(label, ports.get(key, default), port)
            conflict = used_ports.get(selected_port)
            if conflict is None:
                ports[key] = selected_port
                used_ports[selected_port] = label
                break
            print(f"Ошибка: порт {selected_port} уже использует {conflict}")

    section("Команды")
    old_teams = list(cfg["teams"].items())
    count = ask("Количество команд", len(old_teams) or 1, positive_int)
    teams = {}
    for index in range(count):
        default_name = old_teams[index][0] if index < len(old_teams) else f"Team #{index + 1}"
        default_ip = old_teams[index][1] if index < len(old_teams) else "127.0.0.1"
        while True:
            name = ask(f"Команда {index + 1}: имя", default_name, team_name)
            if name not in teams:
                break
            print("Ошибка: имена команд не должны повторяться")
        teams[name] = ask(f"Команда {index + 1}: IP или домен", default_ip, host)
    cfg["teams"] = teams

    section("Флаги и отправка")
    flags["format"] = ask(
        "Регулярное выражение флага",
        flags.get("format", r"[A-Z0-9]{31}="),
        flag_regex,
    )
    flags["lifetime"] = ask(
        "Время жизни флага, секунд", flags.get("lifetime", 300), positive_int
    )
    submitter["flag_limit"] = ask(
        "Флагов за одну отправку", submitter.get("flag_limit", 100), positive_int
    )
    submitter["period"] = ask(
        "Пауза между отправками, секунд", submitter.get("period", 2), positive_int
    )

    section("Чек-система")
    available = sorted(
        item.stem
        for item in (ROOT / "services/S4DFarm/server/app/protocols").glob("*.py")
        if item.stem != "__init__"
    )
    print("Протоколы: " + ", ".join(available))
    selected_protocol = ask(
        "Протокол", checksystem.get("protocol", "ctfcup_tcp"), protocol
    )
    checksystem["protocol"] = selected_protocol
    if selected_protocol in HTTP_PROTOCOLS:
        checksystem["url"] = ask(
            "URL отправки флагов",
            checksystem.get("url", "https://checksystem/api/flags"),
            endpoint_url,
        )
    else:
        checksystem["host"] = ask(
            "IP или домен чек-системы",
            checksystem.get("host", "127.0.0.1"),
            host,
        )
    if selected_protocol in TCP_PROTOCOLS:
        checksystem["port"] = ask(
            "Порт чек-системы", checksystem.get("port", 31337), port
        )

    current_token = secrets.get("CHECKSYSTEM_TOKEN") or str(checksystem.get("token", ""))
    current_team_token = secrets.get("CHECKSYSTEM_TEAM_TOKEN") or str(
        checksystem.get("team_token", "")
    )
    secrets["CHECKSYSTEM_TOKEN"] = ask(
        "Токен чек-системы (Enter — оставить, - — удалить)",
        current_token,
        secret=True,
        optional=True,
    )
    secrets["CHECKSYSTEM_TEAM_TOKEN"] = ask(
        "Токен команды (Enter — оставить, - — удалить)",
        current_team_token,
        secret=True,
        optional=True,
    )
    checksystem["token"] = ""
    checksystem["team_token"] = ""

    section("Neo worker")
    neo["worker_jobs"] = ask(
        "Параллельных задач", neo.get("worker_jobs", 20), positive_int
    )
    neo["ping_every"] = ask(
        "Интервал heartbeat", neo.get("ping_every", "5s"), duration
    )
    neo["submit_every"] = ask(
        "Интервал отправки", neo.get("submit_every", "2s"), duration
    )
    neo["build_client_image"] = ask_yes_no(
        "Автоматически собирать образ воркера",
        bool(neo.get("build_client_image", True)),
    )

    section("GitHub runner")
    print("Можно оставить пустым и настроить runner позже.")
    secrets["GITHUB_RUNNER_URL"] = ask(
        "URL репозитория или организации (Enter — оставить, - — удалить)",
        secrets.get("GITHUB_RUNNER_URL", ""),
        validator=optional_url,
        optional=True,
    )
    secrets["GITHUB_RUNNER_TOKEN"] = ask(
        "Runner token (Enter — оставить, - — удалить)",
        secrets.get("GITHUB_RUNNER_TOKEN", ""),
        secret=True,
        optional=True,
    )
    while secrets["GITHUB_RUNNER_TOKEN"] and not secrets["GITHUB_RUNNER_URL"]:
        print("Ошибка: для runner token нужен URL репозитория или организации")
        secrets["GITHUB_RUNNER_URL"] = ask(
            "URL репозитория или организации",
            "",
            validator=endpoint_url,
        )


def print_summary(cfg, secrets):
    checksystem = cfg["checksystem"]
    selected_protocol = checksystem["protocol"]
    if selected_protocol in HTTP_PROTOCOLS:
        endpoint = checksystem.get("url", "не задан")
    else:
        endpoint = checksystem.get("host", "не задан")
        if selected_protocol in TCP_PROTOCOLS:
            endpoint = f"{endpoint}:{checksystem.get('port', 'не задан')}"

    section("Проверка")
    print(f"Сервер:       {cfg['server']['public_ip']}")
    print(f"Команды:      {len(cfg['teams'])}")
    print(f"Формат флага: {cfg['flags']['format']}")
    print(f"Чек-система:  {selected_protocol}, {endpoint}")
    print(f"Worker jobs:  {cfg['neo']['worker_jobs']}")
    print(
        "Токен чек-системы: "
        + ("задан" if secrets.get("CHECKSYSTEM_TOKEN") else "не задан")
    )
    print(
        "GitHub runner: "
        + (
            f"{secrets.get('GITHUB_RUNNER_URL') or 'URL не задан'}, токен задан"
            if secrets.get("GITHUB_RUNNER_TOKEN")
            else "не настроен"
        )
    )


def main():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit("Мастер настройки нужно запускать в обычном терминале.")

    cfg_path = ROOT / "attdef.yml"
    secrets_path = ROOT / "runtime/secrets.env"
    cfg = parse_simple_yaml(cfg_path)
    secrets = load_env(secrets_path)

    print("Настройка Attack/Defence farm")
    print("Enter оставляет текущее значение. Ctrl+C отменяет настройку.")
    try:
        configure(cfg, secrets)
        print_summary(cfg, secrets)
        if not ask_yes_no("Сохранить конфигурацию", True):
            raise Cancelled
    except (Cancelled, EOFError, KeyboardInterrupt):
        print("\nНастройка отменена. Файлы не изменены.")
        return 130
    except ValueError as exc:
        print(f"\nОшибка: {exc}. Файлы не изменены.")
        return 2

    write_atomic(cfg_path, dump_simple_yaml(cfg) + "\n", 0o644)
    write_secrets(secrets_path, secrets)
    print(f"\nСохранено: {cfg_path}")
    print(f"Секреты: {secrets_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
