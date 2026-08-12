# Attack/Defence_farm

Пакет Attack-Defense фермы на базе S4DFarm и Neo.

Первичная настройка через терминальный мастер:

```sh
./farm init
```

Он запросит адрес сервера, команды, формат флага, чек-систему, параметры воркера и GitHub runner. Повторно открыть мастер: `./farm configure`. Токены сохраняются в `runtime/secrets.env`, этот файл исключен из Git.

Настрой один файл:

```sh
vim attdef.yml
```

Запусти ферму:

```sh
./farm up
```

`./farm up` поднимает S4DFarm, Neo, Redis, VictoriaMetrics, TLS proxy и управляемый воркер.

Публичные URL идут через самоподписанный HTTPS:

- S4DFarm UI: `https://SERVER_IP:5443`
- Neo Web UI: `https://SERVER_IP:8443`

Предупреждение браузера нормально для CTF-сетапа. HTTP-порты UI привязаны к `127.0.0.1`, для команды используй HTTPS.

Показать отпечаток сертификата:

```sh
./farm cert
```

Управление локальным воркером:

```sh
./farm worker status
./farm worker logs
./farm worker restart
./farm worker delete
```

`./farm worker` равен `./farm worker start`. `./farm worker delete` удаляет управляемый воркер и старый `neo-latest`, если он остался от прошлого запуска.

Разовый запуск в текущем терминале:

```sh
./farm worker run
```

## Воркер на другом хосте

Ферма работает на основном сервере. На другом хосте нужны Docker, SSH до сервера и сгенерированный Neo client config. Neo gRPC должен быть доступен как `SERVER_IP:5005`.

На другом хосте:

```sh
SERVER_IP=5.129.237.176
mkdir -p ~/neo-worker
rsync -av root@$SERVER_IP:/opt/vibe_cat_but_sad_farm/services/neo/client_env/ ~/neo-worker/
cd ~/neo-worker
```

Подними туннели до S4DFarm и VictoriaMetrics, потому что они доступны только на localhost основного сервера:

```sh
nohup ssh -N -L 5137:127.0.0.1:5137 -L 8428:127.0.0.1:8428 root@$SERVER_IP > farm-tunnels.log 2>&1 &
echo $! > farm-tunnels.pid
```

Запуск удалённого воркера:

```sh
nohup ./start.sh neo run -j 20 --timeout-autoscale-target 0 > worker.log 2>&1 &
```

Проверка:

```sh
docker ps --filter name=neo-latest
tail -f worker.log
```

Остановка:

```sh
docker rm -f neo-latest
kill "$(cat farm-tunnels.pid)"
```

Не коммить и не шарь `client_config.yml`: там `grpc_auth_key`. Если менял зависимости воркера и пересобирал образ, перенеси образ на другой хост:

```sh
# основной сервер
docker save ghcr.io/c4t-but-s4d/neo_env:latest | gzip > /tmp/neo_env_latest.tar.gz

# другой хост
scp root@$SERVER_IP:/tmp/neo_env_latest.tar.gz /tmp/
gunzip -c /tmp/neo_env_latest.tar.gz | docker load
```

Полезные команды:

```sh
./farm status
./farm password
./farm cert
./farm logs
./farm worker
./farm worker logs
./farm worker delete
./farm neo info
```

Кнопки Neo Web UI:

- `Download`: скачать текущую версию эксплойта.
- `Delete`: удалить эксплойт из состояния Neo server. Воркер перестанет планировать его после heartbeat.

## Как писать exploits

Neo запускает эксплойт как обычный исполняемый файл:

```sh
./exploit.py TARGET_IP
```

Главное правило: эксплойт должен печатать найденные флаги в stdout. Все, что совпадает с `flags.format` из `attdef.yml`, Neo сам вытащит и отправит в S4DFarm.

Минимальный пример:

```python
#!/usr/bin/env python3
import sys
import requests

target = sys.argv[1]
r = requests.get(f"http://{target}:8080/api/search?q=test", timeout=3)
print(r.text, flush=True)
```

Если в ответе есть `FLAG{...}`, этого достаточно.

## Правка зависимостей

Зависимости менять тут:

```text
services/neo/client_env/requirements.txt
```

После изменения:

```sh
./farm build-client
```

Это пересоберет только образ Neo client/worker, ферма не упадет.

Потом надо перезапустить управляемый воркер, чтобы он взял новый образ:

```sh
./farm worker restart
```

Подробности по эксплойтам: [docs/EXPLOITS_RU.md](docs/EXPLOITS_RU.md).

URL по умолчанию:

- S4DFarm UI: `https://SERVER_IP:5443`
- Neo Web UI: `https://SERVER_IP:8443`
- Локальный HTTP S4DFarm UI: `http://127.0.0.1:5137`
- Локальный HTTP Neo Web UI: `http://127.0.0.1:8090`
- VictoriaMetrics: `http://127.0.0.1:8428`, только на сервере.

Не коммить `.env` и сгенерированный Neo client config. Они уже в `.gitignore`.
