# VK Ads — REST API

> Этот файл — справочная документация по прямому REST. Работать можно и через `vk-ads-mcp` (см. `references/vk-ads-mcp-integration.md`) — тогда запуск кода не нужен, — и через наш `scripts/vk_ads_api.py`, которому запуск кода нужен; клиент исправлен и создаёт корректную иерархию (см. ниже).

## ⚠️ Терминология и иерархия (важно!)

API наследует имена от myTarget, которые в новом UI VK Реклама означают другое:

| UI термин ads.vk.ru | API-ресурс | Родитель | Описание |
|---|---|---|---|
| **Кампания** (верх) | `ad_plan` | — | Контейнер верхнего уровня: цель + общий бюджет |
| **Группа объявлений** | `campaign` | `ad_plan_id` | Таргетинги, плейсменты, дневной бюджет |
| **Объявление** | `banner` | `campaign_id` | Конкретный креатив (текст + медиа) |

**Правильная цепочка создания:** `ad_plan` → `campaign` (с `ad_plan_id`) → `banner` (с `campaign_id`).

> В `campaigns` и `ad_groups` API отдаёт одну и ту же сущность под двумя именами — `GET /campaigns.json` и `GET /ad_groups.json` возвращают идентичный список.

**🚨 Gotcha:** `campaign` (группа) без `ad_plan_id` создаётся, но становится **orphan** — невидим в новом UI. А `banner` привязывается к `campaign_id` (к ГРУППЕ), **не** к `ad_plan_id`.

**В нашем `scripts/vk_ads_api.py` это исправлено:** используй `client.create_campaign_tree(ad_plan_payload, groups)` — он атомарно создаёт `ad_plan → campaigns → banners` в правильных связях и статусе `blocked`. Классы: `client.ad_plans` (Кампания), `client.campaigns` (Группа), `client.banners` (Объявление).

**Перед первым массовым созданием в незнакомом кабинете** вызывай `client.probe_schema()` (или CLI `probe-schema`) — он делает GET одного реального объекта каждого уровня и показывает фактические имена полей. Имена полей могли измениться относительно myTarget — не строй payload на догадках.

---

VK Реклама работает через REST API (наследник myTarget API). Базовый URL: `https://ads.vk.com/api/v2/`. Запросы и ответы — JSON.

## Авторизация (OAuth2)

Три схемы:
1. **Client Credentials Grant** — для собственного кабинета (один кабинет = одно приложение)
2. **Agency Client Credentials Grant** — для агентств с клиентами
3. **Authorization Code Grant** — для сторонних приложений с правами на чужой кабинет

### Получение токена (Client Credentials)

```http
POST https://ads.vk.com/api/v2/oauth2/token.json
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials&client_id={ID}&client_secret={SECRET}
```

**Ответ:**
```json
{ "access_token": "...", "token_type": "Bearer", "expires_in": 86400, "refresh_token": "..." }
```

### Использование токена

Каждый запрос: `Authorization: Bearer {access_token}`. При запросе с `scope=offline` токен не истекает.

### Лимиты
- 5 активных токенов на пару `client_id` × user
- Rate limit: 5 req/sec, всплески до 20

---

## Получение `client_id` и `client_secret`

1. Зайти в ads.vk.ru → Настройки → API → Создать приложение
2. Указать название, callback URL (для Authorization Code Grant)
3. Скопировать `client_id` и `client_secret`

**Безопасность:** секрет держать в `.env` или в системе credentials скилла, НЕ в переписке. Клиент подхватывает `.env` из cwd или из папки `scripts/` (см. `from_env`), либо ключ из `manage_credentials`.

---

## Главные ресурсы

### Кампании — `/ad_plans` (UI «Кампания», верхний уровень)
- `GET /ad_plans.json?limit=&offset=` — список
- `GET /ad_plans/{id}.json` — детали
- `POST /ad_plans.json` — создание (может содержать nested `campaigns` для атомарности)
- `PUT /ad_plans/{id}.json` — обновление (в т.ч. `status: active` для активации)

**Объект AdPlan (Кампания):**
```json
{
  "id": 12345,
  "name": "AiSMM — SMM-фрилансеры — конверсии",
  "status": "blocked|active|deleted",
  "objective": "leads|conversions|traffic|reach|video_views|...",
  "budget_limit": 50000,
  "budget_limit_day": 5000,
  "bid_strategy": "minimal_price|max_price",
  "max_price": null
}
```
(Суммы — в **рублях**: `500` = 500 ₽. `objective` и общий бюджет живут на уровне Кампании.)

### Группы объявлений — `/campaigns` (UI «Группа»)
Ребёнок Кампании — обязателен `ad_plan_id`, иначе orphan.

- `GET /campaigns.json?ad_plan_id={id}` — список групп в кампании
- `GET /campaigns/{id}.json`
- `POST /campaigns.json` — создание (нужен `ad_plan_id`)
- `PUT /campaigns/{id}.json`

**Объект Campaign (Группа):**
```json
{
  "id": 67890,
  "ad_plan_id": 12345,
  "name": "H1 — LAL 1% от платных",
  "status": "blocked|active",
  "budget_limit_day": 2000,
  "targetings": {
    "age": [25, 45],
    "sex": [1, 2],
    "geo": {"regions": [1, 2]},
    "interests": ["smm", "marketing"],
    "lookalike_audience_ids": [54321],
    "custom_audience_ids": [],
    "key_phrases": []
  },
  "placements": ["vk_feed", "vk_clips", "ok_feed"]
}
```

### Объявления — `/banners` (UI «Объявление»)
Ребёнок Группы — обязателен `campaign_id` (НЕ `ad_plan_id`).

- `GET /banners.json?_campaign_id={id}` — список объявлений в группе. Фильтры — в
  myTarget-синтаксисе с подчёркиванием (`_campaign_id`, `_ad_plan_id`), так шлёт и хостовый MCP.
  В REST-клиенте вживую не проверено: на случай, если VK фильтр проигнорирует, клиент
  отсеивает чужие объекты по полю `campaign_id` (`only_children`).
- `GET /banners/{id}.json`
- ~~`POST /banners.json`~~ — **такого метода нет**, см. ниже

> 🚨 **Сверено 15.09.2026.** Отдельного создания объявления в API нет: `POST /api/v2/banners.json`
> отвечает `405 unsupported_http_method` с `supported_methods: ["GET"]` — так это описано в коде
> хостового сервера `ai-hub-open/vk-ads-mcp` (`src/tools/banners.ts`), который проверялся против
> ads.vk.com. Объявления создаются **вложенным массивом `banners` внутри payload группы**:
> `campaigns.create({..., "banners": [...]})` или `client.create_campaign_tree(...)`.
> `BannersAPI.create` оставлен заглушкой, которая бросает ошибку с этим объяснением.

- `PUT /banners/{id}.json`

**Объект Banner (объявление на сайт, мультиформат) — форма по ролям:**
```json
{
  "name": "cr_snizu_img_v1",
  "status": "blocked",
  "urls": {"primary": {"id": 123456}},
  "textblocks": {
    "title_40_vkads": {"text": "SMM с AI — экономьте 20 часов"},
    "text_90": {"text": "Полная воронка контента. 7 дней бесплатно."},
    "cta_sites_full": {"text": "visitSite"}
  },
  "content": {
    "icon_256x256": {"id": 111},
    "image_600x600": {"id": 222}
  }
}
```
Сверено вживую через MCP (10.2026). Старые поля `format` / `title` / `description` / `url` /
`images` / `call_to_action` VK отвергает как неизвестные.

- `urls.primary.id` — из `POST /urls.json` с телом `{"url": "<адрес>"}` (`GET` → 405). Одинаковые адреса регистрируй один раз.
- Лимиты ролей: `title_40_vkads` ≤ 40, `text_90` ≤ 90 — не резать молча.
- `image_600x600` — квадрат **ровно 600×600** (1080 отклоняется; роли под квадрат 1080 нет — `banner_patterns`, 19.10.2026). `image_1080x607` / `image_1080x1350` есть в `banner_patterns` (шаблоны 513 / 515), но объявление с ними вживую не создавалось; точный размер — по описанию MCP.
- `about_company_115` («О компании», ≤115) — необязательная роль шаблонов сайта; **по умолчанию не шлём**: данные ОРД VK берёт из настроек кабинета. Только по явному полю `about_company_115` в креативе.
- `icon_256x256` (логотип) обязателен во всех шаблонах сайта (`banner_patterns` 513–515).
- Видео на сайт — `icon_256x256` + `video_landscape_30s` / `video_square_1_1_30s` / `video_portrait_4_5_30s` / `video_portrait_9_16_30s` (`banner_patterns` 321–324, 509–512); в REST-клиенте форма по ролям для видео не реализована. Роли слайдов карусели не разобраны. Справочник `banner_patterns` — 642 шаблона, ~690 тыс. символов.
- Без данных ОРД в кабинете — ошибка `ord_data_required`.

### Аудитории (`/audiences`)
- `GET /audiences.json`, `POST /audiences.json`, `PUT /audiences/{id}.json`
- `POST /audiences/{id}/upload.json` — загрузка списка (CSV для custom_list)

**Типы:** `custom_list`, `lookalike` (нужны `source_id`, `similarity`), `retargeting_pixel`, `community`, `key_phrases`.

### Статистика (`/statistics`)
- `GET /statistics/ad_plans/{ids}/day.json?date_from=&date_to=`
- `GET /statistics/campaigns/{ids}/day.json?...`
- `GET /statistics/banners/{ids}/day.json?...`
- `GET /statistics/{entity}/summary.json?...`

Метрики: `impressions`, `clicks`, `ctr`, `cpc`, `cost`/`spent` (рубли), `goals` (события пикселя), `cpa`, `video_views`.

### Файлы (`/content`)
- `POST /content/static.json` — загрузка изображения → возвращает `id` для `content.<роль>.id` объявления.
- `POST /content/video.json` — загрузка видео → `id` (роль видео в объявлении не подтверждена).

### Пакеты размещения (`/packages`)
- `GET /packages.json` — весь список (~180 пакетов) одним ответом, фильтр по `objective` на клиенте
  (`client.packages.list(objective=...)`, CLI `python -m scripts.vk_ads_api packages --objective <цель>`).
  ⚠️ В REST-клиенте вживую не проверено. `package_id` обязателен при создании группы.
  Без пикселя с целью — пакет с оплатой за клики (`priced_event_type: 1`); oCPM (`30`) требует `priced_goal`.

### Ссылки (`/urls`)
- `POST /urls.json` с `{"url": "..."}` → `id` для `urls.primary.id` объявления. `GET` → 405.
- `POST /content/html5.json` — HTML5-креатив.

⚠️ Общего `/content/upload.json` **не существует** — тип контента задаётся путём.
Приём только `multipart/form-data`, поле `file`; ссылку VK сам не скачивает, файл
шлёт клиент. `GET` по этим путям отвечает `405` — списка загруженного контента в
API нет, сохраняй возвращённые `id`.

### Утилиты (`/dictionaries`)
- `GET /dictionaries/regions.json?country=RU`, `.../interest_categories.json`, `.../call_to_actions.json`

---

## Полный workflow создания кампании

1. **Аудитории первыми** (если нужны custom/LAL):
   ```http
   POST /audiences.json           (custom_list "Платные клиенты")
   POST /audiences/{id}/upload.json
   GET  /audiences/{id}.json       (ждать status=ready)
   POST /audiences.json           (LAL 1% от source_id=<above>)
   ```

2. **Кампания (ad_plan)** — цель + общий бюджет, status=blocked:
   ```http
   POST /ad_plans.json
   {"name":"…","objective":"conversions","status":"blocked",
    "budget_limit":50000,"budget_limit_day":5000,"bid_strategy":"minimal_price"}
   ```

3. **Группы (campaigns)** — по одной на ячейку матрицы, с `ad_plan_id`, status=blocked:
   ```http
   POST /campaigns.json
   {"ad_plan_id":12345,"name":"H1 — LAL 1%","status":"blocked",
    "budget_limit_day":2000,
    "targetings":{"lookalike_audience_ids":[54321],"age":[25,45],"sex":[1,2],"geo":{"regions":[1]}},
    "placements":["vk_feed","vk_clips"]}
   ```

4. **Объявления (banners)** — 3-5 на группу, **вложенным массивом внутри группы**:
   ```http
   POST /content/static.json      (картинка → image_id; квадрат ровно 600×600)
   POST /content/static.json      (логотип → logo_id, 256×256)
   POST /urls.json  {"url":"https://сайт/?utm_source=vk"}   (→ url_id)
   POST /campaigns.json
   {"ad_plan_id":12345,"package_id":<id>,"name":"H1 — LAL 1%","status":"blocked", …,
    "banners":[
      {"name":"cr_v1","status":"blocked",
       "urls":{"primary":{"id":<url_id>}},
       "textblocks":{"title_40_vkads":{"text":"…"},"text_90":{"text":"…"},
                     "cta_sites_full":{"text":"visitSite"}},
       "content":{"icon_256x256":{"id":<logo_id>},"image_600x600":{"id":<image_id>}}}
    ]}
   ```

   🚨 **Отдельного `POST /banners.json` в API нет** — он отвечает
   `405 unsupported_http_method` с `supported_methods: ["GET"]` (сверено с
   реализацией хостового сервера `vk-ads-mcp`, проверявшейся против ads.vk.com).
   Ресурс Banner поддерживает `GET`, `POST` по id (правка), `DELETE` и пакетный
   `POST /banners/remoderate.json`. «Долить» объявления в созданную группу нельзя:
   группа, созданная без баннеров, остаётся пустой — её пересоздают. Проверка залива —
   `GET /banners.json?_campaign_id=<id группы>&fields=id,campaign_id`: провал = пустой список
   (поле `banners` и issue `NO_BANNERS_WITH_ACTIVE_STATUS` у группы на паузе — не признак провала).
   В нашем клиенте `client.banners.create(...)` намеренно бросает ошибку с этим
   объяснением, а `create_campaign_tree` кладёт баннеры внутрь payload группы.

5. **Активация — ТОЛЬКО по подтверждению пользователя И после ОРД-preflight:**
   ```http
   PUT /ad_plans/{id}.json  {"status":"active"}
   ```
   В нашем клиенте: `client.activate_ad_plan(id, confirmation="YES_ACTIVATE")` — сам проверяет, что у объявлений заполнен `company_info` (иначе `OrdPreflightError`), и требует явного подтверждения.

**Проще всего** — одна операция вместо шагов 2-4:
```python
client.create_campaign_tree(ad_plan_payload, [
    {"payload": {group1…}, "banners": [b1, b2]},
    {"payload": {group2…}, "banners": [b3]},
])
```

---

## Обработка ошибок

```json
{"error": {"code": "validation_error", "message": "Field 'title' too long", "fields": {"title": "max 25 characters (site)"}}}
```
Коды: `validation_error`, `unauthorized`, `rate_limit_exceeded`, `not_found`, `forbidden`.

---

## Особенности и подводные камни

1. **Деньги в рублях.** 5000 ₽ = `5000` (подтверждено вживую 16.10.2026: запись `50000` = «50 000 ₽» в кабинете). Не умножай на 100. `spent`/`cpm`/`cpc`/`cpa` в статистике — тоже рубли (строки), а `ctr`/`cr` — **проценты** (`0.5176` = 0.52 %). Скрипты отказываются писать дневной бюджет > 100 000 ₽ и общий > 10 000 000 ₽ без `--confirm-large-budget`: назови пользователю сумму в рублях и добавляй флаг только после его явного «да, сумма верна».
2. **Время в UTC.** Учитывать +3 часа для отчётов по МСК.
3. **Статусы.** API: `blocked` (пауза), `deleted` (мягкое удаление). Жёстко удалить по API нельзя. В UI статусов больше (general/start/campaign_status) — «На модерации», «Отклонена», «Приостановлена по дневному лимиту», «Баланс исчерпан», «Архив». При мониторинге различать причину простоя.
4. **Модерация + ОРД.** После создания объявление идёт на модерацию (`moderating` → `accepted`/`rejected`) и на ОРД (статус «Ожидание» до 10 дней → «Принято» с ERID / «Отклонено»). Запуск считать завершённым после присвоения ERID.
5. **Лимиты (general/start/ad_limits).** Объявлений: общий 300 (растёт с тратами: +1 за 150 ₽), дневной 200/сутки. До 100 групп в кампании, до 30 объявлений в группе, до 10 000 аудиторий в кабинете.
6. **Лимиты на запросы.** 5 req/sec, всплески до 20 → 429.
7. **Загрузка файлов.** `multipart/form-data`. Видео перформанс-объявления — до 90 МБ (general/start/creating). Медийная видеореклама — свои лимиты (branding-formats).
8. **Refresh token.** Обновлять через `refresh_token`, не повторно через `client_credentials`.
9. **Cross-check payload.** Имена полей наследованы от myTarget и могли измениться — перед массовым созданием вызывай `client.probe_schema()`.

---

## Минимальный пример Python (через `scripts/vk_ads_api.py`)

```python
from scripts.vk_ads_api import VKAdsClient

client = VKAdsClient.from_credentials()   # или from_env() — читает .env

# Read
plans = client.ad_plans.list(limit=100)                 # Кампании
groups = client.campaigns.list(ad_plan_id=plans[0]["id"])  # Группы в кампании

# Live-сверка имён полей перед созданием
print(client.probe_schema())

# Создание всего дерева сразу (всё в blocked)
tree = client.create_campaign_tree(
    {"name": "Тест", "objective": "leads", "budget_limit_day": 4000, "bid_strategy": "minimal_price"},
    [{"payload": {"name": "H1", "package_id": 3232, "targetings": {...}},
      "banners": [{"name": "cr_v1",
                   "urls": {"primary": {"id": client.urls.register("https://сайт/")}},
                   "textblocks": {"title_40_vkads": {"text": "…"}, "text_90": {"text": "…"},
                                  "cta_sites_full": {"text": "visitSite"}},
                   "content": {"icon_256x256": {"id": 111}, "image_600x600": {"id": 222}}}]}],
)

# Активация — по явному подтверждению, с ОРД-preflight
client.activate_ad_plan(tree["ad_plan"]["id"], confirmation="YES_ACTIVATE")
```

См. полный код в `scripts/vk_ads_api.py`.

---

## Альтернативы официальному API
- **Improvado / Supermetrics / Roistat** — аналитика (read-only)
- **Albato** — интеграции (zapier-like)
- **eLama** — управление через надстройку

Если пользователь жалуется что API сложно — это валидный путь, но скилл сам через сторонние сервисы работать не будет.
