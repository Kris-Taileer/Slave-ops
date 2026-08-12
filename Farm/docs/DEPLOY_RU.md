# Деплой vibe_cat_but_sad_farm

Минимальный сценарий:

```sh
vim attdef.yml
./farm up
./farm status
./farm password
```

`./farm up` поднимает и основной локальный worker. Отдельно запускать `./farm worker` после деплоя не нужно.

В `attdef.yml` обычно меняешь:

- `server.public_ip`
- `teams`
- `flags.format`
- `flags.lifetime`
- `checksystem.protocol`
- `checksystem.host` / `port` / `url` / `token`

Основные URL:

- S4DFarm: `http://SERVER_IP:5137`
- Neo Web UI: `http://SERVER_IP:8090`
- VictoriaMetrics: `http://127.0.0.1:8428` только с самого сервера.

Пароли:

```sh
./farm password
```

Добавление эксплойтов:

1. Открой Neo Web UI.
2. Загрузи файл или архив.
3. Укажи `id`, `interval`, `timeout`.
4. Сначала можно включить `Upload disabled`, потом нажать `Enable`.

Worker:

```sh
./farm worker
./farm worker status
./farm worker logs
./farm worker restart
./farm worker delete
```

Сырые команды Neo:

```sh
./farm neo info
./farm neo disable my_sploit
./farm neo enable my_sploit
./farm neo tail my_sploit
./farm neo single my_sploit
```

Логи:

```sh
./farm logs
./farm logs s4d-celery
./farm logs neo-server
```

На турнире порядок такой:

1. Вписать реальные команды и submitter в `attdef.yml`.
2. `./farm up`.
3. `./farm status`.
4. Открыть S4DFarm и Neo Web UI.
5. Добавлять эксплойты через Neo Web UI.
6. Проверить worker через `./farm worker status` или `./farm worker logs`.

Если флаги не сабмитятся:

- проверь `flags.format`;
- проверь, что exploit печатает флаги в stdout;
- проверь `./farm logs s4d-celery`;
- проверь `checksystem.protocol` и адрес submitter.
