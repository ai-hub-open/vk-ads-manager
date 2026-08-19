# Подключение MCP-сервера «VK Реклама»

Скилл работает с кабинетом через **хостовый MCP-сервер** организации: `https://vkads-mcp.aihub.click.ru/mcp` (код — <https://github.com/ai-hub-open/vk-ads-mcp>) — 48 инструментов: кампании, группы, объявления, аудитории, бюджеты, статистика. Локально ничего не запускается, Bun и клон репозитория не нужны.

## Подключение

Автоматически (нужен запуск кода):

```bash
python -m scripts.setup_vk_ads_mcp --token <CLICK_RU_TOKEN> --vk-account-id <ID> --target all
```

Установщик пишет блок `mcpServers.vk-ads` в конфиги Cursor (`~/.cursor/mcp.json`), Claude Code (`.mcp.json`) и Claude Desktop (через stdio-мост `mcp-remote`). Флаги: `--target cursor|cursor-project|claude-code|claude-desktop|all`, `--dry-run`, `--remove`. Существующие серверы в конфигах сохраняются.

Вручную — по `docs/hosted-mcp-setup.md` репозитория пакета (там же форматы для каждого клиента).

После подключения перезапусти клиента и проверь связь инструментом `vk_ads_auth_check` — он возвращает данные пользователя VK Ads, если креды валидны.

## Авторизация

Креды передаются HTTP-заголовками на каждый запрос (один инстанс обслуживает несколько аккаунтов). Варианты:

| Вариант | Заголовки |
|---|---|
| **Click.ru (основной)** — OAuth ВК и заявки на API не нужны | `X-Click-Ru-Token: <API-токен click.ru>` + `X-Click-Ru-Account-Id: <ID аккаунта VK Рекламы в click.ru>`; для мастер-аккаунта добавь `X-Click-Ru-User-Id` |
| Готовый токен VK Рекламы | `X-VK-Ads-Token: <access_token>` |
| OAuth-приложение (target.vk.ru) | `X-VK-Ads-Client-Id` + `X-VK-Ads-Client-Secret`; агентство от имени клиента — `+ X-VK-Ads-Agency-Client-Name` |

Токен click.ru: профиль https://click.ru/userinfo.html → «API Token» → «Создать». ID аккаунта VK Рекламы — через `GET /accounts` в https://api.click.ru/V0/docs/. Справка: https://help.click.ru/2327, https://help.click.ru/4814.

Вызов без кредов возвращает ошибку с перечнем нужных заголовков — по ней можно свериться, что шлюз жив.

## Инструменты (48)

Снято с живого сервера (`GET /mcp/tools`, 19.08.2026). Все имена с префиксом `vk_ads_`:

| Область | Инструменты |
|---|---|
| Авторизация и аккаунт | `vk_ads_auth_check`, `vk_ads_account_info`, `vk_ads_token_revoke` |
| Кампании (API `ad_plans`) | `vk_ads_ad_plans_list`, `vk_ads_ad_plans_get`, `vk_ads_ad_plans_create`, `vk_ads_ad_plans_update`, `vk_ads_ad_plans_delete` |
| Группы объявлений (API `campaigns`) | `vk_ads_campaigns_list`, `vk_ads_campaigns_get`, `vk_ads_campaigns_create`, `vk_ads_campaigns_update`, `vk_ads_campaigns_set_status`, `vk_ads_campaigns_delete` |
| Доп. группы (API `ad_groups`) | `vk_ads_ad_groups_list`, `vk_ads_ad_groups_get`, `vk_ads_ad_groups_create`, `vk_ads_ad_groups_update`, `vk_ads_ad_groups_delete` |
| Объявления (API `banners`) | `vk_ads_banners_list`, `vk_ads_banners_get`, `vk_ads_banners_create`, `vk_ads_banners_update`, `vk_ads_banners_moderate`, `vk_ads_banners_delete` |
| Контент | `vk_ads_content_upload_image`, `vk_ads_content_upload_video` |
| Статистика | `vk_ads_statistics_day`, `vk_ads_statistics_summary`, `vk_ads_statistics_breakdown`, `vk_ads_async_report_create`, `vk_ads_async_report_get` |
| Ремаркетинг | `vk_ads_remarketing_segments_list/create/update/delete`, `vk_ads_remarketing_pixels_list/create/delete` |
| Списки пользователей | `vk_ads_users_lists_list`, `vk_ads_users_lists_create`, `vk_ads_users_lists_upload_items`, `vk_ads_users_lists_delete` |
| Агентство | `vk_ads_agency_clients_list`, `vk_ads_agency_clients_create` |
| Справочники | `vk_ads_packages_list`, `vk_ads_regions_search`, `vk_ads_dictionary_get` |

В сессии агента имена могут выглядеть иначе (в одной среде — `mcp__vk-ads__vk_ads_auth_check` и т.п.) — ориентируйся по короткому имени.

### Маппинг задач скилла на инструменты

| Задача (шаг) | Инструменты |
|---|---|
| Аудит аккаунта (Шаг 0.5) | `vk_ads_auth_check`, `vk_ads_account_info`, `vk_ads_ad_plans_list` |
| Фактические CPM/CTR для прогноза (Шаг 9.5) | `vk_ads_statistics_summary`, `vk_ads_statistics_day` (entity `campaigns`/`ad_groups`/`banners`, `ids`, `date_from`/`date_to`) |
| Загрузка креативов (Шаг 11) | `vk_ads_content_upload_image` / `vk_ads_content_upload_video`, параметр `source_path_or_url` — **на хосте только публичный http(s)-URL** (см. ниже) |
| Залив кампании (Шаг 11, путь A) | `vk_ads_ad_plans_create` (`payload` с nested `campaigns: [...]` → `banners: [...]`, атомарно) либо цепочка `vk_ads_ad_plans_create` → `vk_ads_campaigns_create(ad_plan_id=...)` → `vk_ads_banners_create(campaign_id=...)` |
| Пауза/запуск, бюджет (lifecycle) | `vk_ads_campaigns_set_status`, `vk_ads_campaigns_update`, `vk_ads_ad_plans_update` |
| CRM-аудитории и пиксели | `vk_ads_users_lists_*`, `vk_ads_remarketing_pixels_*` |
| Отчёты за период | `vk_ads_statistics_summary` / `vk_ads_statistics_day`; большие срезы — `vk_ads_async_report_create` + `vk_ads_async_report_get` |

## Терминология (UI vs API)

| UI ads.vk.ru | API и инструменты |
|---|---|
| Кампания | `ad_plan` (`vk_ads_ad_plans_*`) |
| Группа объявлений | `campaign` (`vk_ads_campaigns_*`) |
| Объявление | `banner` (`vk_ads_banners_*`) |

🚨 Группа `campaign`, созданная без `ad_plan_id`, становится «сиротой» и невидима в новом кабинете. Создавай ad_plan с nested campaigns атомарно или всегда передавай `ad_plan_id`.

## Ограничения хостового режима

- **Локальные файлы недоступны.** `vk_ads_content_upload_image/video` на хосте принимают только публичный http(s)-URL (чтение файлов с диска сервера выключено, приватные сети блокируются как SSRF-защита). Картинки из `assets/images/` сначала выложи по публичной ссылке — или заливай медиа через путь B (`scripts/deploy_campaign.py` работает с локальными файлами).
- Список ранее загруженных изображений/видео API не отдаёт — сохраняй ID из ответов загрузки.
- Lookalike как отдельная сущность недоступен — используй сегменты, пиксели и списки пользователей.
- У сервера нет встроенной точки подтверждения: вызов на изменение уходит в API сразу. Все сущности создавай в `status: "blocked"`; активация — только маркетологом в кабинете.
- ВК ограничивает число активных OAuth-токенов (≤5); при ошибке `token_limit_exceeded` — `vk_ads_token_revoke`.

## Фолбеки

MCP недоступен → путь B (`scripts/deploy_campaign.py`, наш REST, нужен запуск кода и токен VK Ads) → путь C (ручной залив по `LAUNCH_GUIDE.md`). Выбор пути — Шаг 11 корневого `SKILL.md`; переход на B/C из-за среды помечай как пропуск в `launch_log.md`.

Локальный stdio-запуск сервера (клон репозитория, Bun 1.1+, `bun run src/index.ts`) остаётся для разработки самого сервера — см. README `ai-hub-open/vk-ads-mcp`.
