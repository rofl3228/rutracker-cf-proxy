<img src="assets/icon.svg" alt="" width="96" align="right">

# rutracker-cf-proxy

[![Test & publish image](https://github.com/rofl3228/rutracker-cf-proxy/actions/workflows/docker.yml/badge.svg)](https://github.com/rofl3228/rutracker-cf-proxy/actions/workflows/docker.yml)

Прокси между Prowlarr и rutracker.org, который проходит защиту Cloudflare.
Cookie `cf_clearance` берётся у [byparr](https://github.com/ThePhaseless/Byparr), а все запросы прокси делает сам, притворяясь тем же браузером (`curl_cffi`).
В комплекте Cardigann-определение индексатора для Prowlarr, названия раздач в котором приводятся к формату, понятному Radarr и Sonarr.

Образ (linux/amd64, linux/arm64): [`ghcr.io/rofl3228/rutracker-cf-proxy`](https://github.com/rofl3228/rutracker-cf-proxy/pkgs/container/rutracker-cf-proxy) или [`kirfeo/rutracker-cf-proxy`](https://hub.docker.com/r/kirfeo/rutracker-cf-proxy) — одинаковые теги.

## Зачем

Встроенная связка Prowlarr + FlareSolverr/byparr решает challenge в браузере, но потом повторяет запрос своим .NET-клиентом с полученной cookie.
Cloudflare такой повтор не принимает: `cf_clearance` привязан к User-Agent, TLS-отпечатку браузера и IP-адресу. Вдобавок byparr не умеет POST, поэтому логин на RuTracker через него невозможен.

Этот прокси берёт у byparr только cookie и User-Agent, а сами запросы (логин, поиск, скачивание `.torrent`) отправляет через `curl_cffi` с отпечатком того же браузера и с того же IPv4.

Кэшируются только результаты поиска (`tracker.php`) с хотя бы одной раздачей, отдельно для каждой сессии rutracker. Логин, проверка входа и скачивание `.torrent` всегда идут на сайт. В ответе есть заголовок `X-Cache: HIT/MISS/SHARED`, статистика — в `/health`.

Prowlarr обращается к прокси как к обычному сайту: `http://127.0.0.1:30240/forum/tracker.php?...` уходит на `https://rutracker.org/forum/tracker.php?...`.
Прокси переписывает `Location` и `Set-Cookie` так, чтобы сессия rutracker жила у Prowlarr, а cookie Cloudflare остаётся внутри прокси.

## Как устроено

| Файл | Что делает |
|---|---|
| `src/rutracker_proxy/config.py` | настройки из переменных окружения |
| `src/rutracker_proxy/challenge.py` | узнаёт страницу Cloudflare «Just a moment...» |
| `src/rutracker_proxy/byparr.py` | просит byparr решить challenge, забирает `cf_clearance` и User-Agent |
| `src/rutracker_proxy/clearance.py` | хранит clearance, обновляет его один раз на всех, сохраняет в `/data` |
| `src/rutracker_proxy/upstream.py` | запрос к rutracker; при challenge обновляет clearance и повторяет один раз |
| `src/rutracker_proxy/passthrough.py` | маршрут прокси: переписывает заголовки, cookie и ссылки, отдаёт ошибки 502/503/504 |
| `src/rutracker_proxy/cache.py` | кэш поиска на 5 минут и объединение одинаковых одновременных запросов |
| `src/rutracker_proxy/app.py` | HTTP-сервер, `/health` |
| `src/rutracker_proxy/definition.py` | команда `install-definition`: кладёт определение в Prowlarr и просит его перечитать |
| `src/rutracker_proxy/definitions/rutracker-proxy.yml` | Cardigann-определение индексатора |
| `scripts/smoke_test.py` | сквозная проверка через запущенный прокси: логин, поиск, `.torrent` |

## Переменные окружения

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `PORT` | `8080` | порт прокси (в приложении TrueNAS — `30240`) |
| `BYPARR_URL` | `http://127.0.0.1:30230/v1` | адрес byparr |
| `UPSTREAM_URL` | `https://rutracker.org` | зеркало rutracker |
| `CLEARANCE_URL` | `https://rutracker.org/forum/login.php` | страница, которую открывает byparr |
| `IMPERSONATE` | `firefox` | каким браузером притворяться; должен совпадать с браузером byparr |
| `IP_FAMILY` | `4` | `4` / `6` / `any`; cookie привязана к IP, byparr ходит по IPv4 |
| `UPSTREAM_TIMEOUT` | `90` | таймаут запроса к rutracker, с |
| `ORIGIN_RETRIES` | `1` | повторы GET при ошибках Cloudflare 520–524 (сервер rutracker не ответил); POST не повторяется |
| `UPSTREAM_CONCURRENCY` | `2` | сколько запросов к rutracker одновременно |
| `CACHE_TTL` | `300` | сколько секунд хранить результаты поиска (`0` — выключить кэш) |
| `CACHE_MAX_ENTRIES` | `100` | сколько результатов поиска держать в памяти |
| `BYPARR_TIMEOUT` | `120` | сколько byparr может решать challenge, с |
| `CLEARANCE_MAX_AGE` | `0` | обновлять clearance заранее, когда старше N секунд (`0` — только по факту challenge) |
| `CLEARANCE_FILE` | `/data/clearance.json` в Docker | где хранить clearance между перезапусками |
| `LOG_LEVEL` | `INFO` | `DEBUG` покажет каждый запрос |

## Запуск на TrueNAS

Apps → Discover Apps → Custom App → Install via YAML, вставить [deploy/truenas-app.yaml](deploy/truenas-app.yaml) (поправить `TZ`, адрес byparr и путь к данным; каталог данных должен быть доступен на запись uid 568).
Прокси и byparr должны выходить в интернет с одного внешнего IP — на одном хосте это так и есть.

Проверка:

```bash
curl http://127.0.0.1:30240/health
```

Обновление: Apps → rutracker-proxy → Update / Redeploy (подтянет свежий `latest`).

Диагностика — один раз пройти Cloudflare и выйти:

```bash
sudo docker run --rm --network host kirfeo/rutracker-cf-proxy check /forum/tracker.php?nm=test
```

`OK: ... -> 302 ... location=.../login.php` означает, что Cloudflare пройден (302 на логин — нормальный ответ гостю).

Сквозная проверка через запущенный прокси (логин и пароль из `.env`, одна попытка входа):

```bash
sudo docker run --rm --network host --env-file .env -v $PWD/scripts:/scripts --entrypoint python kirfeo/rutracker-cf-proxy /scripts/smoke_test.py http://127.0.0.1:30240
```

## Подключение к Prowlarr

Определение индексатора лежит внутри образа, в Prowlarr его подкладывает одноразовый сервис `prowlarr-definition` из [deploy/truenas-app.yaml](deploy/truenas-app.yaml). При каждом запуске или обновлении приложения он:
1. записывает `rutracker-proxy.yml` в `Definitions/Custom/` Prowlarr (только если содержимое изменилось; ручные правки файла перезаписываются);
2. ставит в `links` адрес из `PUBLIC_URL`; прежний адрес по умолчанию попадает в `legacylinks`, и Prowlarr сам переключает на новый уже добавленные индексаторы;
3. если заданы `PROWLARR_URL` и `PROWLARR_API_KEY`, просит Prowlarr перечитать определения (команда `IndexerDefinitionUpdate`) — перезапуск не нужен. Без ключа изменения применятся после перезапуска Prowlarr;
4. завершается.

Папка `Definitions/Custom` должна существовать и быть доступна на запись uid 568. Вручную то же самое: `docker run --rm -v <папка>:/definitions -e PUBLIC_URL=... kirfeo/rutracker-cf-proxy install-definition`.

Добавление индексатора:
1. Prowlarr → Indexers → Add Indexer → найти **RuTracker (proxy)**.
2. Указать логин и пароль rutracker. **Не** назначать тег FlareSolverr.
3. Test → Save.

Исходник определения — `src/rutracker_proxy/definitions/rutracker-proxy.yml`. Категории генерируются из `definitions/rutracker_forums.tsv`: после правок в `scripts/gen_categories.py` запустить `python scripts/gen_categories.py`.

| Переменная `install-definition` | По умолчанию | Смысл |
|---|---|---|
| `DEFINITION_DIR` | `/definitions` | куда класть определение |
| `PUBLIC_URL` | `http://127.0.0.1:30240/` | адрес прокси, как его видит Prowlarr |
| `PROWLARR_URL` | — | адрес Prowlarr для перезагрузки определений |
| `PROWLARR_API_KEY` | — | API-ключ Prowlarr (Settings → General) |

## Сборка образа

GitHub Actions ([.github/workflows/docker.yml](.github/workflows/docker.yml)): тесты на каждый push и pull request, затем сборка для amd64/arm64.
Публикация в Docker Hub и GHCR — при push в `main` (тег `latest`) и при тегах `vX.Y.Z` (теги `X.Y.Z` и `X.Y`); у каждой сборки есть тег `sha-<commit>`.

Нужные секреты репозитория: `DOCKERHUB_USERNAME` и `DOCKERHUB_TOKEN` (Docker Hub → Account settings → Personal access tokens, права Read & Write). Для GHCR используется встроенный `GITHUB_TOKEN`.

## Разработка

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Linux/macOS: .venv/bin/python
.venv/Scripts/python -m pytest
```
