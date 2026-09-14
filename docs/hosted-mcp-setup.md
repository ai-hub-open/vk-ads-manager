# Хостовый MCP «VK Реклама» — подключение

Как подключить скилл `vk-ads-manager` к хостовому MCP-серверу VK Рекламы. Локальный запуск (Bun, клон репозитория) не нужен — сервер уже поднят на стороне aihub.

## Серверы

| Сервер в конфиге | URL | Что даёт |
|---|---|---|
| `vk-ads` | `https://vkads-mcp.aihub.click.ru/mcp` | 45 инструментов VK Ads API (`vk_ads_*`: кампании, группы, объявления, аудитории, статистика) |
| `keepimage` (хранилище картинок) | `https://storage.aihub.click.ru/mcp` | Временное файловое хранилище: публикует картинку → публичная ссылка без авторизации, живёт ≤2 ч. Нужно, чтобы заливать локальные картинки в VK (`vk_ads_content_upload_image`). Инструменты: `storage_publish_image`, `storage_list`, `storage_info`, `storage_delete`. HTTP API для больших файлов — `PUT/POST /v1/objects`. Только картинки (PNG/JPEG/GIF/WebP), не видео |

Корневой путь `/` отдаёт 404 — рабочий JSON-RPC endpoint именно `/mcp`. Health-check: `GET /healthz` → `OK`.

## Авторизация

Всё сводится к **одному API-токену click.ru**: профиль https://click.ru/userinfo.html → поле «API Token» → «Создать». Аккаунт VK Рекламы должен быть подключён в click.ru.

| Сервер | Заголовки на каждый запрос |
|---|---|
| `vk-ads` | `X-Click-Ru-Token: <CLICK_RU_TOKEN>`, `X-Click-Ru-Account-Id: <ID аккаунта VK Рекламы в click.ru>`; для мастер-аккаунта добавить `X-Click-Ru-User-Id` |
| `keepimage` | Токен click.ru любым из трёх равнозначных способов: заголовок `X-Auth-Token: <CLICK_RU_TOKEN>`, `Authorization: Bearer <CLICK_RU_TOKEN>` или токен в адресе `/c/<CLICK_RU_TOKEN>[/<user-id>]/mcp`. Мастер-аккаунту добавить `X-Auth-UserId: <ID пользователя>`. **Тот же токен click.ru, что и у `vk-ads`** — сервер сам это подтверждает в ошибке 401. Установщик `setup_vk_ads_mcp.py` подключает `keepimage` header-способом; скрипт `scripts/upload_creatives_to_storage.py` ходит в KeepImage по HTTP с `X-Auth-Token` (коннектор ему не нужен) |

Примечания:

- Список инструментов VK Ads открыт без кредов (`GET /mcp/tools`), но вызовы без заголовков возвращают ошибку «Не заданы креды VK Ads…» с перечнем нужных заголовков.
- ID аккаунта VK Рекламы в click.ru: `GET /accounts` в https://api.click.ru/V0/docs/. Он же приходит в поле `account_id` инструмента `vk_ads_accounts_list`.
- Альтернативы click.ru (готовый `X-VK-Ads-Token`, OAuth `X-VK-Ads-Client-Id` + `X-VK-Ads-Client-Secret`, для агентства `+ X-VK-Ads-Agency-Client-Name`) сервер тоже принимает.
- Токен click.ru — секрет. В git не коммитим: в репозитории только плейсхолдеры, реальные значения пишутся в конфиги клиентов установщиком.

## Автоматическая запись конфигов (рекомендуется)

Установщик умеет цели `cursor` (глобально, `~/.cursor/mcp.json`), `cursor-project` (`.cursor/mcp.json` в текущей папке), `claude-code` (`.mcp.json` в текущей папке), `claude-desktop`, `all`:

```bash
python -m scripts.setup_vk_ads_mcp \
  --token <CLICK_RU_TOKEN> --vk-account-id <ID_АККАУНТА> \
  --target all
```

Полезные флаги: `--dry-run` (показать, что будет записано), `--remove` (удалить записи), `--click-ru-user-id` (мастер-аккаунт click.ru), `--vk-ads-token` (готовый токен VK вместо click.ru). Токен можно не передавать аргументом, если он уже сохранён через `manage_credentials set clickru`.

Персональную ссылку установщик тоже умеет — заголовки в этом случае не пишутся:

```bash
python -m scripts.setup_vk_ads_mcp \
  --connection-url 'https://vkads-mcp.aihub.click.ru/o/<connection-id>/<token>' \
  --target claude-code
```

`keepimage` этим путём не подключается: токена click.ru в ссылке нет. Если нужна заливка локальных картинок — добавь его отдельно, вручную по таблице выше. В выводе установщика токен маскируется (`ogBN...fH`), в конфиг пишется целиком.

Установщик **не запускает никаких процессов** и не требует Bun — он только дописывает `mcpServers` в конфиги (с бэкапом, существующие серверы сохраняются).

При подключении по пути click.ru установщик заодно:
- подключает коннектор `keepimage` (`https://storage.aihub.click.ru/mcp`, header `X-Auth-Token`) — инструмент `storage_publish_image` для заливки картинок в средах без запуска кода. Отключить: `--no-keepimage`. Снимается вместе с `vk-ads` при `--remove` (если не задан `--no-keepimage`);
- сохраняет токен click.ru в реестр ключей (`clickru`, а с `--click-ru-user-id` — ещё и `clickru_user_id`), поэтому скрипт `scripts/upload_creatives_to_storage.py` работает сразу — отдельный `manage_credentials set clickru` не нужен.

Ни то, ни другое не выполняется при `--dry-run` (только сообщается) и на пути с готовым `--vk-ads-token` (токен click.ru не используется).

## Ручная настройка

### Cursor — глобально `~/.cursor/mcp.json` или проектно `.cursor/mcp.json`

```json
{
  "mcpServers": {
    "vk-ads": {
      "url": "https://vkads-mcp.aihub.click.ru/mcp",
      "headers": {
        "X-Click-Ru-Token": "<CLICK_RU_TOKEN>",
        "X-Click-Ru-Account-Id": "<ID_АККАУНТА_VK>"
      }
    },
    "keepimage": {
      "url": "https://storage.aihub.click.ru/mcp",
      "headers": {
        "X-Auth-Token": "<CLICK_RU_TOKEN>"
      }
    }
  }
}
```

Существующие серверы (например Figma) не затирать — блоки дописываются рядом.

### Claude Code — `.mcp.json` в корне проекта

Тот же блок, но у каждого сервера добавить `"type": "http"`:

```json
{
  "mcpServers": {
    "vk-ads": {
      "type": "http",
      "url": "https://vkads-mcp.aihub.click.ru/mcp",
      "headers": {
        "X-Click-Ru-Token": "<CLICK_RU_TOKEN>",
        "X-Click-Ru-Account-Id": "<ID_АККАУНТА_VK>"
      }
    }
  }
}
```

### Claude Desktop — `claude_desktop_config.json`

Claude Desktop не принимает произвольные HTTP-заголовки в конфиге напрямую, поэтому запись идёт через stdio-мост `mcp-remote` (нужен Node.js):

```json
{
  "mcpServers": {
    "vk-ads": {
      "command": "npx",
      "args": [
        "-y", "mcp-remote", "https://vkads-mcp.aihub.click.ru/mcp",
        "--header", "X-Click-Ru-Token: <CLICK_RU_TOKEN>",
        "--header", "X-Click-Ru-Account-Id: <ID_АККАУНТА_VK>"
      ]
    }
  }
}
```

Путь к конфигу: macOS `~/Library/Application Support/Claude/claude_desktop_config.json`, Windows `%APPDATA%\Claude\claude_desktop_config.json`, Linux `~/.config/Claude/claude_desktop_config.json`.

### Коннектор по персональной ссылке (claude.ai, Claude Desktop «Connectors»)

Среды, которые не умеют передавать произвольные заголовки, подключаются по **персональной ссылке подключения** вида:

```text
https://vkads-mcp.aihub.click.ru/o/<connection-id>/<token>
```

Креды в ней уже зашиты — ни `X-Click-Ru-*`, ни `--header` дописывать не нужно. Ссылка выдаётся на стороне aihub под конкретное подключение; суффикс `/mcp` на конце допустим, но не обязателен. Такая ссылка **равнозначна паролю к рекламному кабинету** — не публикуй её в issue, чатах и конфигах, попадающих в git.

## Проверка после подключения

После записи конфига **перезапусти клиент** (Cursor: Settings → MCP — сервер должен стать зелёным; Claude Desktop: полный выход и запуск). Затем в сессии агента:

1. Вызови `vk_ads_auth_check` — должен вернуть данные пользователя VK Ads (`ok: true`, поле `user`).
2. Вызови `vk_ads_accounts_list` — покажет доступные кабинеты; значение `account_id` из ответа передаётся любому другому инструменту одноимённым параметром.

Если сервер не появился: проверь URL (ровно `/mcp` на конце — либо персональная ссылка `/o/<connection-id>/<token>`), токен и перезапуск клиента. Ошибка 401 — токен click.ru недействителен или заголовок назван иначе, чем ждёт шлюз (сверься с таблицей выше).

## Фолбек: локальный stdio

Хостовый вариант — дефолт. Локальный запуск (клон `ai-hub-open/vk-ads-mcp`, Bun 1.1+, `bun run src/index.ts`) остаётся для отладки и разработки самого сервера — см. README репозитория. Скилл в этом случае работает так же: он ищет сервер по имени (`vk-ads`) и коротким именам инструментов, а не по способу запуска.
