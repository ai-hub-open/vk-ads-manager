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
  "budget_limit": 5000000,
  "budget_limit_day": 500000,
  "bid_strategy": "minimal_price|max_price",
  "max_price": null
}
```
(Суммы — в копейках. `objective` и общий бюджет живут на уровне Кампании.)

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
  "budget_limit_day": 200000,
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

- `GET /banners.json?campaign_id={id}` — список объявлений в группе
- `GET /banners/{id}.json`
- `POST /banners.json` — создание (нужен `campaign_id`)
- `PUT /banners/{id}.json`

**Объект Banner (универсальная запись):**
```json
{
  "id": 11111,
  "campaign_id": 67890,
  "status": "moderating|accepted|rejected|blocked",
  "format": "universal",
  "title": "SMM с AI — экономьте 20 часов",
  "description": "Полная воронка контента. 7 дней бесплатно.",
  "url": "https://aismm.pro/ru?utm_source=vk_ads&utm_campaign=...",
  "call_to_action": "learn_more",
  "images": [{"id": "...", "url": "..."}],
  "videos": [],
  "company_info": "ИП Михалева Н.С., ИНН 121525316700"
}
```
`company_info` — юр.данные для ОРД. Заполнять при рекламе товаров с онлайн-продажей; для услуг/офлайн оставлять пустым (см. `vk-ads-specs.md` → модерация).

### Аудитории (`/audiences`)
- `GET /audiences.json`, `POST /audiences.json`, `PUT /audiences/{id}.json`
- `POST /audiences/{id}/upload.json` — загрузка списка (CSV для custom_list)

**Типы:** `custom_list`, `lookalike` (нужны `source_id`, `similarity`), `retargeting_pixel`, `community`, `key_phrases`.

### Статистика (`/statistics`)
- `GET /statistics/ad_plans/{ids}/day.json?date_from=&date_to=`
- `GET /statistics/campaigns/{ids}/day.json?...`
- `GET /statistics/banners/{ids}/day.json?...`
- `GET /statistics/{entity}/summary.json?...`

Метрики: `impressions`, `clicks`, `ctr`, `cpc`, `cost` (в копейках!), `goals` (события пикселя), `cpa`, `video_views`.

### Файлы (`/content`)
- `POST /content/upload.json` — загрузка изображения/видео → возвращает `id` для `banners.images`/`videos`.

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
    "budget_limit":5000000,"budget_limit_day":500000,"bid_strategy":"minimal_price"}
   ```

3. **Группы (campaigns)** — по одной на ячейку матрицы, с `ad_plan_id`, status=blocked:
   ```http
   POST /campaigns.json
   {"ad_plan_id":12345,"name":"H1 — LAL 1%","status":"blocked",
    "budget_limit_day":200000,
    "targetings":{"lookalike_audience_ids":[54321],"age":[25,45],"sex":[1,2],"geo":{"regions":[1]}},
    "placements":["vk_feed","vk_clips"]}
   ```

4. **Объявления (banners)** — 3-5 на группу, с `campaign_id`:
   ```http
   POST /content/upload.json      (картинка → image_id)
   POST /banners.json
   {"campaign_id":67890,"format":"universal","title":"…","description":"…",
    "url":"…","call_to_action":"learn_more","images":[{"id":"<image_id>"}],
    "company_info":"ИП …, ИНН …"}
   ```

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

1. **Деньги в копейках.** 5000 ₽ = 500000. `cost` в статистике тоже в копейках.
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
    {"name": "Тест", "objective": "leads", "budget_limit_day": 400000, "bid_strategy": "minimal_price"},
    [{"payload": {"name": "H1", "targetings": {...}, "placements": ["vk_feed"]},
      "banners": [{"format": "universal", "title": "…", "url": "…", "company_info": "ИП …, ИНН …", "images": [{"id": 1}]}]}],
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
