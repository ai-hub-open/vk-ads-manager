# Подключение MCP-сервера «VK Реклама»

Скилл работает с кабинетом через **хостовый MCP-сервер** организации: `https://vkads-mcp.aihub.click.ru/mcp` (код — <https://github.com/ai-hub-open/vk-ads-mcp>) — 46 инструментов: кампании, группы, объявления, ссылки, аудитории, бюджеты, статистика. Локально ничего не запускается, Bun и клон репозитория не нужны.

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
| **Персональная ссылка подключения** — для сред без своих заголовков | Заголовков нет: креды зашиты в адрес `https://vkads-mcp.aihub.click.ru/o/<connection-id>/<token>` (суффикс `/mcp` допустим, но не обязателен). Так подключаются коннекторы claude.ai и Claude Desktop, которые произвольные заголовки передавать не умеют. Ссылка равнозначна паролю к кабинету — не публикуй её в issue, чатах и конфигах, попадающих в git |

Токен click.ru: профиль https://click.ru/userinfo.html → «API Token» → «Создать». ID аккаунта VK Рекламы — через `GET /accounts` в https://api.click.ru/V0/docs/. Справка: https://help.click.ru/2327, https://help.click.ru/4814.

Вызов без кредов возвращает ошибку с перечнем нужных заголовков — по ней можно свериться, что шлюз жив.

## Инструменты (46)

Снято с живого сервера 26.08.2026, `vk_ads_urls_create` добавлен 16.10.2026. Все имена с префиксом `vk_ads_`:

| Область | Инструменты |
|---|---|
| Авторизация и аккаунт | `vk_ads_auth_check`, `vk_ads_account_info`, `vk_ads_accounts_list`, `vk_ads_token_revoke` |
| Кампании (API `ad_plans`) | `vk_ads_ad_plans_list`, `vk_ads_ad_plans_get`, `vk_ads_ad_plans_create`, `vk_ads_ad_plans_update`, `vk_ads_ad_plans_delete` |
| Группы объявлений (API `campaigns`) | `vk_ads_campaigns_list`, `vk_ads_campaigns_get`, `vk_ads_campaigns_create`, `vk_ads_campaigns_update`, `vk_ads_campaigns_set_status`, `vk_ads_campaigns_delete` |
| Те же группы под алиасом `ad_groups` | `vk_ads_ad_groups_list`, `vk_ads_ad_groups_get`, `vk_ads_ad_groups_create`, `vk_ads_ad_groups_update`, `vk_ads_ad_groups_delete` |
| Объявления (API `banners`) | `vk_ads_banners_list`, `vk_ads_banners_get`, `vk_ads_banners_update`, `vk_ads_banners_remoderate`, `vk_ads_banners_delete` — **создания нет**, см. ниже |
| Контент | `vk_ads_content_upload_image`, `vk_ads_content_upload_video` |
| Ссылки | `vk_ads_urls_create` — регистрирует адрес сайта (`POST /urls.json`) и отдаёт `id`. Обычно не нужен: create-инструменты принимают адрес строкой и регистрируют сами |
| Статистика | `vk_ads_statistics_day`, `vk_ads_statistics_summary` |
| Ремаркетинг | `vk_ads_remarketing_segments_list/create/update/delete`, `vk_ads_remarketing_pixels_list/create/delete` |
| Списки пользователей | `vk_ads_users_lists_list`, `vk_ads_users_lists_create`, `vk_ads_users_lists_upload_items`, `vk_ads_users_lists_delete` |
| Агентство | `vk_ads_agency_clients_list`, `vk_ads_agency_clients_create` |
| Справочники | `vk_ads_packages_list`, `vk_ads_regions_search`, `vk_ads_dictionary_get` |

### 🚨 Объявление нельзя создать отдельным вызовом

В API нет `POST /banners`. Объявления создаются **только** вложенным массивом внутри группы:

`vk_ads_campaigns_create(payload={..., "banners": [{...}, {...}]})`

Либо целиком атомарно: `vk_ads_ad_plans_create(payload={..., "campaigns": [{..., "banners": [...]}]})` —
`ad_plans_create` принимает вложенные `campaigns`, а `campaigns_create` принимает вложенные `banners`.
Бюджеты в этих payload'ах (`budget_limit`, `budget_limit_day`) — **в рублях**: 500 ₽ = 500.

Создать группу, а потом «долить» в неё объявления — **нельзя**. Группа, созданная без баннеров,
остаётся пустой навсегда — её удаляют и пересоздают.

**Форма объявления «на сайт» (сверено вживую через MCP, 10.2026):**

```json
{"name": "cr_v1", "status": "blocked",
 "urls": {"primary": "https://сайт/?utm_source=vk"},
 "textblocks": {"title_40_vkads": {"text": "Заголовок до 40"},
                "text_90": {"text": "Текст до 90"},
                "cta_sites_full": {"text": "visitSite"}},
 "content": {"icon_256x256": {"id": <id логотипа>}, "image_600x600": {"id": <id картинки>}}}
```

- `urls.primary` — адрес строкой: MCP сам регистрирует ссылку и подставит `{"id": ...}`. В REST — сначала `POST /api/v2/urls.json` с `{"url": "..."}` (GET → 405), затем `{"primary": {"id": <url_id>}}`.
- Старые поля `format` / `title` / `description` / `url` / `images` VK отвергает как неизвестные.
- `image_600x600` принимает квадрат **ровно 600×600** — 1080×1080 отклоняется (живое наблюдение; в самих шаблонах размерных ограничений нет). Роли под квадрат 1080 нет (`banner_patterns`, 19.10.2026): `image_1080x1080` не существует.
- **Справочник `banner_patterns`** (`vk_ads_dictionary_get(name="banner_patterns")`, проверено 19.10.2026) — 642 шаблона, ~690 тыс. символов: в чат не тянуть. Шаблоны объявления на сайт (VK Ads, активные): 514 `multiformat_square_site_vkads` — обязательны `primary` (url), `icon_256x256`, `title_40_vkads`, `text_90`, `cta_sites_full`, `image_600x600`; 513 `multiformat_landscape_site_vkads` — то же с `image_1080x607`; 515 `multiformat_portrait_site_vkads` — с `image_1080x1350`; 66 — портрет 9:16 с `image_607x1080`. Необязательные: `title_30_additional`, `text_long`, `about_company_115`. Значит, **логотип `icon_256x256` обязателен** во всех шаблонах сайта. Роли `image_1080x607` / `image_1080x1350` есть, но объявление с ними вживую ещё не создавалось; точный размер 1080×607 / 1080×1350 — по описанию MCP. `about_company_115` — необязательная роль, по умолчанию **не передавай**: данные ОРД VK берёт из настроек кабинета.
- Видео на сайт (справка, скилл форму по ролям для видео не собирает): шаблоны 321–324 и 509–512 — `icon_256x256` + `video_landscape_30s` / `video_square_1_1_30s` / `video_portrait_4_5_30s` / `video_portrait_9_16_30s` (и `*_180s`). Карусель (548, 614–617) — роли слайдов в справочнике без имени, не разобраны.
- Описание `vk_ads_dictionary_get` в MCP пока не упоминает `targetings_tree` и `banner_patterns` — оба имени рабочие.
- Без данных ОРД в кабинете (ИНН и название рекламодателя) создание падает с `ord_data_required`, а атомарный `ad_plans_create` не создаёт ничего. Повторять бесполезно — сначала заполнить данные в кабинете.

### Как проверить, что объявления залились

Смотри **список объявлений группы**: `vk_ads_banners_list(campaign_id=<id группы>, fields="id,name,status,moderation_status")` (в API `campaign_id` = id группы). Провал — только пустой список.

⚠️ Поле `banners` в `vk_ads_ad_groups_get` / `campaigns_get` у группы на паузе (`blocked`) **всегда пустое**, даже когда объявления есть, а issue `NO_BANNERS_WITH_ACTIVE_STATUS` у группы на паузе ожидаем. По ним залив не проверяй — будет ложная тревога.

### `campaigns` и `ad_groups` — одна сущность

`vk_ads_campaigns_list` и `vk_ads_ad_groups_list` возвращают **идентичный результат** —
это не два уровня иерархии, а два имени одного ресурса. Иерархия трёхуровневая:
`ad_plan` → `campaign` (= `ad_group`) → `banner`.

По умолчанию используй `vk_ads_campaigns_*`. Набор `vk_ads_ad_groups_*` оставлен
для совместимости — по возможностям он ничем не отличается.

🚨 **`issues` (диагностика, почему группа не крутится) приходят только по явному
запросу.** И `campaigns_get`, и `ad_groups_get` без `fields` отдают три поля
(`id`, `name`, `package_id`) — ни статуса, ни issues. Запрашивай явно:

`vk_ads_campaigns_get(campaign_id=..., fields="id,name,status,issues")`

Иначе пустой ответ читается как «с группой всё в порядке», хотя она может стоять
с `ARCHIVED` или `NO_BANNERS_WITH_ACTIVE_STATUS` (у **активной** группы это значит «нет живых
объявлений»; у группы на паузе этот issue ожидаем — объявления проверяй через `banners_list`). Неизвестные имена в `fields`
сервер молча игнорирует, ошибки не будет.

В сессии агента имена могут выглядеть иначе (в одной среде — `mcp__vk-ads__vk_ads_auth_check` и т.п.) — ориентируйся по короткому имени.

### Маппинг задач скилла на инструменты

| Задача (шаг) | Инструменты |
|---|---|
| Выбор кабинета (режим click.ru, мультиаккаунт) | `vk_ads_accounts_list` → `account_id` передавать в каждый последующий вызов |
| Аудит аккаунта (Шаг 0.5) | `vk_ads_auth_check`, `vk_ads_account_info`, `vk_ads_ad_plans_list` |
| Фактические CPM/CTR для прогноза (Шаг 9.5) | `vk_ads_statistics_summary`, `vk_ads_statistics_day` (entity `campaigns`/`ad_groups`/`banners`, `ids`, `date_from`/`date_to`). Значения лежат в `items[].total.base.cpm` / `…base.ctr`, деньги — строками; см. «Особенности ответов сервера» |
| Загрузка креативов (Шаг 11) | `vk_ads_content_upload_image` / `vk_ads_content_upload_video`, параметр `source_path_or_url` — **на хосте только публичный http(s)-URL**; локальные картинки выкладывай через мост KeepImage (`scripts/upload_creatives_to_storage.py`), см. ниже |
| Залив кампании (Шаг 11, путь A) | `vk_ads_ad_plans_create` с nested `campaigns: [...]` → `banners: [...]` — один атомарный вызов. Либо `vk_ads_ad_plans_create` → `vk_ads_campaigns_create(ad_plan_id=..., banners=[...])` по одной группе за вызов. Отдельного создания баннера нет |
| Пауза/запуск, бюджет (lifecycle) | `vk_ads_campaigns_set_status`, `vk_ads_campaigns_update`, `vk_ads_ad_plans_update` |
| CRM-аудитории и пиксели | `vk_ads_users_lists_*`; пиксель — только `vk_ads_remarketing_pixels_list` (`limit` ≤ 50) для `id`. Создаёт пиксель человек в кабинете (Сайты → Добавить пиксель): VK требует `url`, `email` и пароль от счётчика Top@Mail.ru, пароль в чате не спрашиваем — `vk_ads_remarketing_pixels_create` сам не вызывай |
| Выбор пакета размещения (Шаг 11, обязателен) | `vk_ads_packages_list(objective=...)` → `id` активного пакета. Всего ~174 пакета, фильтруй по `objective`; значения — в `available_objectives` ответа. Нет пикселя с целью — пакет с оплатой за клики (`priced_event_type: 1`), среди них — мультиформат для сайта (`multiformat_all_cpc` в имени; 19.10.2026 это 3232, рядом есть резервный CPC 4844); oCPM (`30`) требует `priced_goal` от пикселя |
| Резолв гео в `region_id` (Шаги 6.5, 11) | `vk_ads_regions_search(query="<город>")` → `items[].id` в `targetings.geo.regions` |
| ID интересов (Шаг 6.5) | `vk_ads_dictionary_get(name="targetings_tree")` — ~71 тыс. символов, ключи `interests` и `interests_soc_dem`; ищи по `name` и `synonyms`, не печатай целиком; появится `vk_ads_interests_search` — через него по названию |
| Проверка залива (Шаг 11) | `vk_ads_banners_list(campaign_id=<id группы>, fields="id,name,status,moderation_status")` — пустой список = провал |
| Повторная модерация отклонённых | `vk_ads_banners_list(fields="id,user_can_request_remoderation")` → `vk_ads_banners_remoderate(banner_ids=[...])` |
| Отчёты за период | `vk_ads_statistics_summary` (агрегат) / `vk_ads_statistics_day` (по дням). Оба требуют явный список `ids` — режима «по всему кабинету» нет, сначала `vk_ads_campaigns_list`. Асинхронных отчётов и разрезов в MCP нет |

## Терминология (UI vs API)

| UI ads.vk.ru | API и инструменты |
|---|---|
| Кампания | `ad_plan` (`vk_ads_ad_plans_*`) |
| Группа объявлений | `campaign` (`vk_ads_campaigns_*`) |
| Объявление | `banner` (`vk_ads_banners_*`) |

🚨 Группа `campaign`, созданная без `ad_plan_id`, становится «сиротой» и невидима в новом кабинете. Создавай ad_plan с nested campaigns атомарно или всегда передавай `ad_plan_id`.

## Ограничения хостового режима

- **Локальные файлы недоступны.** `vk_ads_content_upload_image/video` на хосте принимают только публичный http(s)-URL (чтение файлов с диска сервера выключено, приватные сети блокируются как SSRF-защита). Картинки из `assets/images/` сначала выкладывай в хранилище **KeepImage** — `python -m scripts.upload_creatives_to_storage --workspace <path>` (токен click.ru `clickru`, спрашивается в онбординге) даёт публичные ссылки (`assets/storage_manifest.json`, живут ≤2 ч) и заодно ужимает квадраты больше 600 до 600×600; их и передавай в `source_path_or_url`. **base64 картинки модель в вызов не передаёт** (`data_base64` у `vk_ads_content_upload_image` и `storage_publish_image` — для приложений: 1080×1080 — сотни тысяч символов); допустимо разве что для крошечного файла до ~50 КБ (логотип 256×256). Нет токена или запуска кода — путь B, путь C или прямые публичные ссылки от пользователя. KeepImage хостит только картинки (PNG/JPEG/GIF/WebP) — **видео** заливай публичной ссылкой клиента или через путь B (`scripts/deploy_campaign.py` работает с локальными файлами). Правила к ссылкам, которые надо предъявлять клиенту, — в `references/vk-ads-specs.md` → «Требования к ссылкам на изображения от клиента». Подключение KeepImage — `docs/hosted-mcp-setup.md`.
- Список ранее загруженных изображений/видео API не отдаёт — сохраняй ID из ответов загрузки.
- Lookalike как отдельная сущность недоступен — используй сегменты, пиксели и списки пользователей.
- У сервера нет встроенной точки подтверждения: вызов на изменение уходит в API сразу. Все сущности создавай в `status: "blocked"`; активация — только маркетологом в кабинете.
- ВК ограничивает число активных OAuth-токенов (≤5); при ошибке `token_limit_exceeded` — `vk_ads_token_revoke`.
- **У `ad_plan` нет `set_status`.** Пауза/запуск «Кампании» целиком — только `vk_ads_ad_plans_update`
  с `{"status": ...}`. У `campaign` метод есть.
- **`package_id` обязателен при создании группы** и определяет формат, площадки и модель оплаты.
  После создания не меняется — другая модель оплаты означает новую группу.
  Известные значения `priced_event_type`: 0 = CPM, 1 = CPC, 30 = oCPM, 51 = оплата за лид
  (оба активных пакета `leadads` — 3215 и 4618 — приходят с 51). Список не закрыт: не выводи
  модель оплаты из одного только числа, сверяйся с именем пакета (`_cpm_` / `_cpc_` / `_ocpm_`).
- **В выдаче `packages_list` встречаются пакеты со `status: "blocked"`** — фильтруй по `active`.

## Особенности ответов сервера

**Единицы.** `budget_limit` и `budget_limit_day` — **в рублях** и при чтении, и при записи
(`6000000` = 6 000 000 ₽ в кабинете; запись `500` даёт 500 ₽). Пересчёт не нужен.
**Бюджет передавай в рублях: 500 ₽ = 500.** Не умножай на 100. Дневной бюджет > 100 000 ₽ или общий > 10 000 000 ₽ не пиши без явного подтверждения пользователя (назови сумму в рублях).
`ctr` и `cr` в статистике — **проценты** (`0.5176` = 0.52 %), умножать на 100 не нужно.

**Списки по умолчанию бедные.** `campaigns_list` / `ad_groups_list` / `banners_list`
без параметра `fields` отдают только `id`, `name`, `package_id`. Ни статуса, ни бюджета,
ни `ad_plan_id`. Рабочий набор:
`fields="id,name,status,objective,budget_limit,budget_limit_day,ad_plan_id,created"`.
(`ad_plans_list` — исключение, он и без `fields` добавляет `event_limit`,
`uniq_shows_limit`, `uniq_shows_period`, `yclients_salon_id`, но статуса там тоже нет.)

**Конверт ответа не унифицирован.** `ad_plans_list` → `{count, items, offset}`;
`remarketing_segments_list` → `{limit, offset, items, count}`;
`remarketing_pixels_list` → `{items}` без `count`;
`regions_search` → `{count, items, total_regions}`;
`packages_list` → `{count, total_packages, available_objectives, items}`.
Не строй пагинацию на обязательном наличии `count`.

**У статистики конверт свой, с вложенностью по группам метрик:**

```json
{"items": [{"id": 150765736, "total": {"base": {"shows": 0, "clicks": 0, "cpm": "0", "ctr": 0}}}],
 "total": {"base": {…}}}
```

Метрика лежит по пути `items[].total.<группа>.<имя>` — для прогноза Шага 9.5 это
`items[].total.base.cpm` и `…base.ctr`. Деньги (`spent`, `cpm`, `cpc`, `cpa`) приходят
**строками**, приводи к числу сам; `ctr` и `cr` — числа и уже **проценты**, а не доли (608 кликов / 117 469 показов → `ctr: 0.5176` = 0.52 %). В `forecast_inputs.json` метрика с `source: "api"` читается как проценты (делится на 100); долю пиши с `"unit": "fraction"`.

**Формат ошибки не унифицирован.** У словарей `detail` — строка
(`{"status":404,"detail":"Not found"}`), у сущностей — объект
(`{"status":404,"detail":{"error":{"code":"not_found","message":"Not found"}}}`).
403 дополнительно отдаёт `required_permission` — по нему видно, каких прав не хватает
(например, `view_clients` для `vk_ads_agency_clients_list`).

**Справочники.** `vk_ads_dictionary_get` рабочие имена: `currencies`, `countries`, `regions`,
`targetings_tree`, `banner_patterns` (последние два проверены 19.10.2026; в описании инструмента их пока нет).
Имена `interests`, `sectors`, `browsers`, `languages`, `os` отвечают 404 —
не строй на них таргетинг. ID интересов бери из `targetings_tree`: ~71 тыс. символов, ключи `interests`
(тематические, 364 узла) и `interests_soc_dem` (соцдем, 99 узлов); узел — `id`, `name`, `synonyms`,
`no_checkbox` (заголовок категории), `children`. Ищи по `name` и `synonyms`, целиком не печатай (есть
запуск кода — сохрани в файл и ищи скриптом); без запуска кода ответ может не поместиться — интересы
словами и подбор в кабинете с пометкой о пропуске. В какое поле `targetings` уходят ID из
`interests_soc_dem` — не проверено. Когда появится `vk_ads_interests_search` — поиск по
названию через него. Для гео вместо полного дерева используй `vk_ads_regions_search`.

**Форма `targetings` при создании групп** — `SKILL.md` Шаг 11 п.0.3 и `references/vk-ads-api.md`
(сверено на живых группах): `age.age_list` по годам, `sex` строками, `interests` числами. Описание
`vk_ads_ad_plans_create` / `vk_ads_campaigns_create` в MCP схемы `targetings` не даёт — на ошибке
валидации документацию VK API в интернете не ищи, действуй по п.0.3.

## Фолбеки

MCP недоступен → путь B (`scripts/deploy_campaign.py`, наш REST, нужен запуск кода и токен VK Ads) → путь C (ручной залив по `LAUNCH_GUIDE.md`). Выбор пути — Шаг 11 корневого `SKILL.md`; переход на B/C из-за среды помечай как пропуск в `launch_log.md`.

Локальный stdio-запуск сервера (клон репозитория, Bun 1.1+, `bun run src/index.ts`) остаётся для разработки самого сервера — см. README `ai-hub-open/vk-ads-mcp`.
