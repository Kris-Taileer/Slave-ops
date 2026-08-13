# Блоки — конвейер скриптов (Jenkins-подобный)

Вкладка **Блоки** в панели Aether. Логические блоки — это скрипты (Python или
POSIX `sh`, **не bash**), которые выполняются по очереди и возвращают результат.
Всё крутится в **одном контейнере**; блоки — дочерние процессы `backend.py`, а не
отдельные контейнеры, поэтому с `network_mode: host` их порты торчат прямо на хост.

## Модель

Блок:

| поле | смысл |
|---|---|
| `type` | `python` или `sh` (POSIX, dash) |
| `mode` | `task` — отрабатывает до конца (успех = exit 0, превышение `timeout` = «завис»); `service` — держит порт (успех = жив после `start_period`, выход = ошибка) |
| `venv` | python: запускать в собственном `blocks/<id>/.venv` (+ опц. `requirements`) |
| `args` | статические аргументы argv |
| `depends_on` | связанные блоки (рёбра графа) |
| `pass_stdout` | передать stdout прямых предков в argv этого блока |
| `port` | service: TCP-порт для проверки «поднялся» |
| `timeout` | task: секунд до вердикта «завис» |

Статусы: `idle · queued · running · success · error · hanging · blocked · stopped`.

### Связи

- **Связанные блоки** (соединены рёбрами `depends_on`) образуют цепочку. Если блок
  упал/завис/остановлен — его потомки становятся `blocked` и не запускаются, пока
  вышестоящий не починят и не перезапустят (fix-and-rerun: правишь скрипт в
  редакторе, `Run` — на успехе цепочка едет дальше сама).
- **Несвязанные блоки** (нет рёбер) работают независимо: падение одного не трогает
  остальных.

### Передача данных

`pass_stdout` = stdout прямых предков подставляется в argv потомка (путь «argc/argv»).
Без него — просто последовательный прогон без данных.

## UI

- Авто-граф: колонки по топологии, SVG-рёбра (зеленеют на успехе предка, краснеют на
  провале). Цвет узла = статус.
- Кнопка на блоке: `▶` Run, `■` Stop, `✎` открыть.
- Инспектор: форма (тип/режим/venv/args/timeout/port/зависимости/pass_stdout),
  CodeMirror-редактор скрипта (моды python/shell), живой вывод, Run/Stop/Restart/Save/Delete.
- Тулбар: «Загрузить примеры», «+ Блок», «Запустить всё», «Стоп».

## API (Bearer-токен из `state/token`)

```
GET    /api/blocks                 список + рёбра + статусы + level (колонка)
POST   /api/blocks                 создать {name, type, script, ...}
GET    /api/blocks/{id}            блок целиком + исходник
PUT    /api/blocks/{id}            обновить конфиг и/или скрипт
DELETE /api/blocks/{id}            удалить
POST   /api/blocks/{id}/run|stop|restart
GET    /api/blocks/{id}/output?since=N   инкрементальный tail вывода
POST   /api/pipeline/run|stop      весь DAG
POST   /api/pipeline/presets       досоздать демо-пресеты
```

## Запуск

Контейнер (Linux-таргет):

```bash
docker compose up --build -d
docker compose logs -f        # там же напечатан токен
```

Панель по умолчанию на `127.0.0.1:8080` (см. `MONITOR_BIND` в `compose.yml`).
Наружу — через SSH-туннель или файрвол: запуск произвольных скриптов по сети — это
RCE по дизайну (как Jenkins), токен лишь гейтит API.

Локально без Docker (например, на macOS, где host-сеть Docker урезана):

```bash
mkdir -p state && head -c 24 /dev/urandom | base64 | tr -d '=+/\n' > state/token
mkdir -p web && ln -sf ../index.html web/ && ln -sf ../script web/ && ln -sf ../style web/
python3 backend.py --root . --monitor-dir . --port 8080 --bind 127.0.0.1
# токен: cat state/token  (панель спросит его в браузере)
```

## Демо-пресеты

При первом запуске (пустое состояние) или по кнопке «Загрузить примеры»
создаётся демо-пайплайн ([pipeline/presets.py](pipeline/presets.py)):

- `gen-token → transform → report` — связанная цепочка с передачей данных через
  argv (`report` — sh-блок);
- `flaky` — независимый, падает намеренно (покажи, что цепочка не страдает; поправь
  `exit(1)`→`exit(0)` и `Run` — увидишь fix-and-rerun);
- `venv-demo` — python в своём `.venv`;
- `web-svc` — service, держит порт 8099 (при host-сети доступен с хоста).

## Сервисы intro (bandiera, blocconote)

Реальные CTF-сервисы из [Ferr0x/ad-ctf-infra](https://github.com/Ferr0x/ad-ctf-infra)
(`challenges/intro/services`) лежат в [services/](services/). Цепочки для их поднятия —
набор пресетов `intro`: кнопка **«Сервисы (intro)»** или `POST /api/pipeline/presets`
с телом `{"set":"intro"}`.

**blocconote** (Python/Flask) — поднимается нативно в контейнере блоков:

```
blocconote-setup (sh) ──▶ blocconote (python·service·venv, :5000) ──▶ blocconote-check (python)
```

- `blocconote-setup` — создаёт `notes/`.
- `blocconote` — service в собственном `.venv` (ставит Flask), слушает `:5000`.
  Порт можно переопределить первым аргументом argv (напр. `5055` — на macOS `:5000`
  занимает AirPlay). Не забудь тогда сменить и поле `port`, и argv у чек-блока.
- `blocconote-check` — task: кладёт и читает заметку через API, проверяет round-trip.

**bandiera** (Node + MySQL) — поднимается через `docker compose` (нужен MySQL):

```
bandiera-up (sh) ──▶ bandiera-check (sh)
```

- `bandiera-up` — `docker compose up -d --build` в `services/bandiera` (app `:8181`, mysql `:3306`).
- `bandiera-check` — ждёт API и кладёт флаг через `POST /bandiera`.

bandiera требует **docker на хосте**: смонтируй `/var/run/docker.sock` в контейнер блоков
(в `compose.yml` добавь `- /var/run/docker.sock:/var/run/docker.sock` и поставь docker CLI
в образ). blocconote докера не требует.

## Тесты

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

`tests/test_store.py` — DAG/персист; `tests/test_runner.py` — исполнение на реальных
процессах (task/service, timeout/hang, venv, sh, передача argv, каскад, fix-and-rerun).
