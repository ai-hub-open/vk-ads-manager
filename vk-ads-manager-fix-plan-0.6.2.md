# План правок `vk-ads-manager` → 0.6.2

**Кому:** Cursor / исполнителю правок в репозитории скилла
**Основание:** аудит пакета 0.6.1 против живого MCP-сервера `VKads` (`https://vkads-mcp.aihub.click.ru/mcp`), 26.08.2026.
Все факты ниже подтверждены **реальными вызовами** к кабинету `vkads_486007@vk@14029134`, а не чтением кода. Выдержки — в Приложении А.

---

## 0. TL;DR для исполнителя

Сверка 0.6.1 сделана чисто, но остановилась на Шаге 11. Осталось три класса проблем:

1. **Залив упадёт на первом вызове** — нигде не добывается обязательный `package_id`, нигде не резолвится гео в `region_id`.
2. **`references/lifecycle-runbook.md` живёт в перевёрнутой иерархии** (`ad_plan` = группа). Это половина скилла — весь lifecycle-режим. План 0.6.1 вынес его в «не трогать» с формулировкой «ссылок на MCP не содержит»; формулировка верна, вывод — нет: проблема не в ссылках, а в модели данных.
3. **Мелкие расхождения имён и фолбеков** — неверный параметр, запрет по несуществующему имени метода, «пиксель только через UI» при живом `vk_ads_remarketing_pixels_create`.

Порядок работы: раздел 2.1 и 2.2 (блокеры) → 2.6 (рунбук, основной объём) → остальное → чеклист приёмки в разделе 4.

Версия после правок — **0.6.2**, запись в `CHANGELOG.md` обязательна.

---

## 1. Сводная таблица расхождений

### 1.1. Блокеры залива

| # | Что не так | Где | Доказательство |
|---|---|---|---|
| B1 | `package_id` не добывается нигде в workflow. `vk_ads_campaigns_create` объявляет `name` + `package_id` обязательными. `vk_ads_packages_list` упомянут только в справочной таблице `vk-ads-mcp-integration.md:49` | `SKILL.md` Шаг 11 путь A (строки 772–782) | Живая группа в кабинете имеет `package_id: 3215`; без него создание не проходит |
| B2 | Гео не резолвится. Аудитории Шага 6.5 описывают города словами, `targetings.geo.regions` требует числовые id. `vk_ads_regions_search` упомянут только в `vk-ads-mcp-integration.md:49,130` | `SKILL.md` Шаги 6.5 и 11 | Живой таргетинг: `"geo": {"regions": [188]}` |

### 1.2. Перевёрнутая иерархия (`ad_plan` ↔ `campaign`)

| # | Что не так | Где |
|---|---|---|
| H1 | `GET /ad_plans/{id}` подаётся как «текущий таргетинг группы», `PUT /ad_plans/{id}.json` — как правка таргетинга группы | `references/lifecycle-runbook.md:154,158,159` (Сценарий 7) |
| H2 | `POST /ad_plans.json` — «копия группы»; `POST /banners.json` с `ad_plan_id` | `lifecycle-runbook.md:271–272` (Сценарий 12) |
| H3 | `GET /ad_plans/?campaign_id={id}`, `POST /ad_plans.json` с новым `campaign_id`, `POST /banners.json` с новым `ad_plan_id` | `lifecycle-runbook.md:283–288` (Сценарий 13) |
| H4 | `PUT /ad_plans/{id}.json` как «добавить в существующие группы» | `lifecycle-runbook.md:102` (Сценарий 4) |
| H5 | «Создаст campaign (status=blocked) → Создаст ad_plans (по числу аудиторий)» — инверсия в описании пути B. Сам `deploy_campaign.py` делает правильно (`create_campaign_tree`: ad_plan → campaigns → banners) | `SKILL.md:816–817` |

**Доказательство правильной модели** (живой ответ `vk_ads_campaigns_list`): у сущности `campaign` есть `ad_plan_id`, `targetings` (age/sex/geo/pads/fulltime), `budget_limit`, `objective`, `package_id`, `issues`. То есть таргетинг и бюджет живут на **campaign (= Группе)**, а `ad_plan` — только контейнер. Иерархия: `ad_plan` → `campaign` (= `ad_group`) → `banner`.

### 1.3. Несуществующие ресурсы

| # | Что не так | Где |
|---|---|---|
| R1 | `GET /audiences.json`, `POST /audiences.json` с типами `lookalike` / `custom_list` | `lifecycle-runbook.md:97,100,101,118,120` (Сценарии 4, 5) |
| R2 | `POST /banners.json` как штатный способ создания — против собственного «отдельного создания объявления нет» в `vk-ads-mcp-integration.md:51–61` | `lifecycle-runbook.md:272,288`; `scripts/vk_ads_api.py:BannersAPI.create` |

На сервере вместо `/audiences` — `vk_ads_remarketing_segments_*` (4 метода) и `vk_ads_users_lists_*` (4 метода).

### 1.4. Мелкие

| # | Что не так | Где |
|---|---|---|
| M1 | `vk_ads_ad_plans_get(id=...)` — параметр называется `ad_plan_id` | `SKILL.md:780` |
| M2 | «Создать пиксель — только UI» при живом `vk_ads_remarketing_pixels_create` | `SKILL.md:958`, `SKILL.md:875` («Что НЕ делает deploy_campaign»), `lifecycle-runbook.md:301` |
| M3 | Guardrail «не вызывай `*_delete`, `campaigns/activate`» — такого имени в MCP нет; активация это `vk_ads_campaigns_set_status(status="active")` и `vk_ads_ad_plans_update({"status":"active"})`. Запрет по несуществующему имени не срабатывает | `SKILL.md:896` |
| M4 | У `ad_plan` **нет** `set_status` (в наборе 45 инструментов его нет). Пауза «Кампании» целиком — только `vk_ads_ad_plans_update`. Нигде не сказано | `vk-ads-mcp-integration.md`, `lifecycle-runbook.md` Сценарий 3 |
| M5 | `vk_ads_banners_remoderate` есть в таблице, но не используется ни в одном сценарии; не описан пред-чек `user_can_request_remoderation` | `vk-ads-mcp-integration.md:43`, рунбук Сценарий 6 |
| M6 | Пост-релизная оптимизация идёт сырым REST `GET /statistics/campaigns/{id}/day.json`, хотя MCP объявлен primary | `SKILL.md:885` |
| M7 | `vk_ads_api.py audiences-list` без MCP-ветки | `SKILL.md:286` |
| M8 | Открытый вопрос из плана 0.6.1 про `POST /banners` не закрыт, а путь B на нём стоит | `scripts/vk_ads_api.py` |

### 1.5. Что сверено и расхождений НЕ имеет — не трогать

- Таблица 45 инструментов в `vk-ads-mcp-integration.md:39–49` — совпадает с живым `tools/list` имя в имя по всем 11 областям.
- Фантомов (`vk_ads_banners_create`, `vk_ads_banners_moderate`, `vk_ads_statistics_breakdown`, `vk_ads_async_report_*`) в пакете не осталось.
- Раздел «Особенности ответов сервера» (копейки, бедные `*_list`, неунифицированный конверт и формат ошибки) — подтверждён: живой ответ отдал `budget_limit: 6000000` и конверт `{count, items, offset}`.
- Обязательный `ids` у `vk_ads_statistics_summary` / `_day` — подтверждён схемой.
- Рабочие имена словарей `currencies` / `countries` / `regions` и 404 у `interests` / `sectors` / `browsers` / `languages` / `os`.
- `references/forecasting.md` — «разрезов через MCP нет» верно, метода нет.

---

## 2. Правки по файлам

### 2.1. `SKILL.md`, Шаг 11 путь A — вставить пре-флайт `package_id` и гео *(блокер B1, B2)*

**Найти** (строка 774, пункт 0 пути A) и **вставить после него два новых пункта**, сдвинув нумерацию:

```markdown
0.1. **Пакет размещения (`package_id`) — обязателен, без него создание группы падает.**
   `vk_ads_packages_list(objective="<цель>")` → выбери пакет и зафиксируй его `id` в `_state.json`.
   Правила отбора: (1) только `status: "active"` — в выдаче встречаются `blocked`;
   (2) `priced_event_type` определяет модель оплаты — `0` = CPM, `1` = CPC, `30` = oCPM;
   (3) при равных условиях бери пакет с `banner_format_id: 0` (мультиформат) —
   он не привязывает тебя к одному формату креатива.
   Всего на сервере ~174 пакета, поэтому **всегда фильтруй по `objective`**, не тяни полный список.
   Доступные значения `objective` приходят в поле `available_objectives` того же ответа;
   для воронки этого скилла рабочие — `leadads` (лид-формы) и `site_conversions` (конверсии на сайте).
   Покажи пользователю выбранный пакет словами («оплата за показы, мультиформат») и подтверди.

0.2. **Гео → `region_id`.** `targetings.geo.regions` принимает **числовые id**, а не названия.
   Для каждого города/региона из `audiences.json` — `vk_ads_regions_search(query="<название>")`,
   возьми `id` из `items`. Полное дерево (`vk_ads_dictionary_get(name="regions")`) не тяни — оно
   большое (5500+ регионов). Ничего не нашлось или нашлось несколько — **спроси пользователя**,
   какой регион имелся в виду, не угадывай. Зафиксируй маппинг «название → id» в `_state.json`.
```

**Найти** строку 780 и **заменить** `vk_ads_ad_plans_get(id=...)` на `vk_ads_ad_plans_get(ad_plan_id=...)` *(M1)*.

**Дописать** в конец пункта 6 (проверка):

```markdown
   Полезные поля для диагностики: `vk_ads_campaigns_list(fields="id,name,status,objective,budget_limit,budget_limit_day,ad_plan_id,package_id,targetings,issues")`
   — `issues` показывает словами, почему группа не крутится (`NO_BANNERS_WITH_ACTIVE_STATUS`,
   `NO_MONEY`, `AD_PLAN_STOPPED`, `STOPPED`).
```

### 2.2. `SKILL.md:816–817` — инверсия в описании пути B *(H5)*

**Найти:**

```
   - Создаст campaign (status=blocked)
   - Создаст ad_plans (по числу аудиторий, всё blocked)
   - Создаст banners с привязкой медиа (всё blocked)
```

**Заменить на:**

```
   - Создаст ad_plan — «Кампанию» верхнего уровня (status=blocked)
   - Создаст campaigns — «Группы объявлений» по числу аудиторий, каждая с `ad_plan_id` (всё blocked)
   - Создаст banners — «Объявления» с привязкой медиа, каждое с `campaign_id` (всё blocked)
```

Это приводит текст в соответствие с тем, что скрипт уже делает (`create_campaign_tree`).

### 2.3. `SKILL.md:896` — guardrail по реальным именам *(M3, M4)*

**Найти:**

```
**Безопасность:** не вызывай `*_delete`, `campaigns/activate` автоматически. Только `*_update` и `audiences/create`.
```

**Заменить на:**

```markdown
**Безопасность.** Никогда не вызывай автоматически:
`vk_ads_*_delete` (любые), `vk_ads_campaigns_set_status(status="active")`,
`vk_ads_ad_plans_update({"status": "active"})`, `vk_ads_banners_update({"status": "active"})`.
Разрешено без отдельного «запускай»: чтение, `*_update` неструктурных полей после «ОК»,
создание сегментов и списков.

⚠️ У `ad_plan` **нет** метода `set_status` — пауза и запуск «Кампании» целиком идут только
через `vk_ads_ad_plans_update({"status": "blocked"|"active"})`. У `campaign` метод есть:
`vk_ads_campaigns_set_status`.
```

### 2.4. `SKILL.md:885` и `SKILL.md:286` — MCP-ветка вместо сырого REST *(M6, M7)*

Строка 885, **заменить:**

```
1. `GET /statistics/campaigns/{id}/day.json?date_from=...&date_to=...` (см. `vk-ads-api.md`).
```

на:

```markdown
1. Статистика за период: `vk_ads_statistics_day(entity="campaigns", ids="<id через запятую>", date_from=..., date_to=...)`.
   ⚠️ `ids` обязателен — режима «по всему кабинету» нет, сначала `vk_ads_campaigns_list`.
   MCP недоступен — прямой REST `GET /statistics/campaigns/{id}/day.json` (см. `vk-ads-api.md`).
```

Строка 286, **заменить:**

```
**Если API доступен** — `vk_ads_api.py audiences-list` от похожих кампаний. Не дублируй уже залитые.
```

на:

```markdown
**Если API доступен** — посмотри, что уже есть: `vk_ads_remarketing_segments_list` (сегменты)
и `vk_ads_users_lists_list` (загруженные CRM-базы). Через наш REST — `vk_ads_api.py audiences-list`.
Не дублируй уже залитые.
```

### 2.5. `SKILL.md:954–961` и `SKILL.md:875` — таблица фолбеков *(M2)*

**Найти** строку 958 и **заменить:**

```
| Создать пиксель (только использовать готовый) | UI ads.vk.ru |
```

на:

```
| ~~Создать пиксель~~ — **умеет**: `vk_ads_remarketing_pixels_create` | — (фолбек не нужен) |
| Проверить, что пиксель стреляет | Только UI / браузер — API отдаёт факт существования, а не события |
```

В блоке «Что НЕ делает deploy_campaign в MVP» (строка ~875) **заменить**:

```
- ❌ Не создаёт пиксель (это делается через UI Шаг 7а + клиент)
```

на:

```
- ❌ Не создаёт пиксель — путь B этого не умеет. Через MCP умеет: `vk_ads_remarketing_pixels_create` (см. Шаг 7а)
```

В Шаге 7а (строка ~362) **дописать** после списка того, что генерит `vk_pixel_helper.py`:

```markdown
**Если MCP подключён** — пиксель можно создать не руками: `vk_ads_remarketing_pixels_create(payload={...})`,
затем `vk_ads_remarketing_pixels_list` для проверки и получения `pixel_id`. Установка кода на сайт
и проверка срабатываний всё равно остаются за человеком — API отдаёт факт существования счётчика,
а не поток событий.
```

### 2.6. `references/lifecycle-runbook.md` — основной объём *(H1–H4, R1, R2, M5)*

Файл переписывается под правильную иерархию **и** получает MCP-первый путь. Это не косметика: сейчас
исполнитель, идущий по Сценарию 7 или 13, создаёт мусор в кабинете.

**Шапка, строка 3.** Заменить:

```
Использовать через `scripts/vk_ads_api.py` либо UI ads.vk.ru.
```

на:

```markdown
Приоритет путей: **MCP-инструменты `vk_ads_*`** → наш REST `scripts/vk_ads_api.py` → UI ads.vk.ru.
Каждый сценарий ниже даёт MCP-вызовы; REST-эквиваленты — в `references/vk-ads-api.md`.

## Иерархия — читать до всех сценариев

`ad_plan` (UI «Кампания») → `campaign` = `ad_group` (UI «Группа объявлений») → `banner` (UI «Объявление»).

**Таргетинг, бюджет, цель и пакет живут на `campaign` (Группе), а не на `ad_plan`.**
`ad_plan` — контейнер: имя, даты, общие лимиты показов. Правишь аудиторию или бюджет — правишь `campaign`.
```

**Строка 8.** Заменить перечисление `(campaigns, ad_plans, banners, audiences)` на
`(ad_plans, campaigns, banners, segments, users_lists)`.

**Сценарий 1 (строки 30–31).** Заменить на:

```markdown
1. `vk_ads_campaigns_list(fields="id,name,status,objective,budget_limit,budget_limit_day,ad_plan_id,issues", limit=100)`
   ⚠️ Без `fields` вернутся только `id`, `name`, `package_id` — ни статуса, ни бюджета.
2. Для активных: `vk_ads_statistics_day(entity="campaigns", ids="<id через запятую>", date_from=<сегодня-7>, date_to=<сегодня>)`
   ⚠️ `ids` обязателен, режима «по всему кабинету» нет.
3. Агрегировать по cost, impressions, clicks, CTR, CPC, conversions, CPL.
   ⚠️ `budget_limit`/`budget_limit_day` приходят **в копейках** (`6000000` = 60 000 ₽), `ctr` — доля, не проценты.
   Пользователю показывай пересчитанные значения.
```

**Сценарий 2 (бюджет, строки 64–67).** `GET/PUT /campaigns/{id}` → `vk_ads_campaigns_get(campaign_id=...)` /
`vk_ads_campaigns_update(campaign_id=..., payload={"budget_limit_day": <копейки>})`. Явно предупредить про копейки.

**Сценарий 3 (пауза/запуск, строка 81).** Переписать с разделением уровней:

```markdown
1. `vk_ads_campaigns_get(campaign_id=..., fields="id,name,status,ad_plan_id,issues")` — текущий статус.
2. Пауза/запуск **группы**: `vk_ads_campaigns_set_status(campaign_id=..., status="blocked"|"active")`.
3. Пауза/запуск **всей Кампании**: `vk_ads_ad_plans_update(ad_plan_id=..., payload={"status": "blocked"|"active"})`
   — ⚠️ у `ad_plan` метода `set_status` нет, только `update`.
4. Группа стоит, хотя статус `active` → смотри `issues`: `AD_PLAN_STOPPED` (остановлен родитель),
   `NO_MONEY` (нет средств), `NO_BANNERS_WITH_ACTIVE_STATUS` (нет живых объявлений).
```

**Сценарий 4 (lookalike, строки 97–102) и Сценарий 5 (CRM-база, строки 118–120).**
Ресурса `/audiences.json` не существует — переписать на реальные:

```markdown
Сценарий 4 (похожие аудитории):
1. `vk_ads_remarketing_segments_list` — что уже есть.
2. Источник похожести — сегмент на базе пикселя или загруженного списка.
3. После «ОК» — `vk_ads_remarketing_segments_create(payload={"name": ..., "pass_condition": 1,
   "relations": [{"object_type": "remarketing_users_list"|"remarketing_counter", "params": {...}}]})`
4. `vk_ads_remarketing_segments_list` — проверка.
⚠️ Lookalike как отдельная сущность в API/MCP недоступен. Строится через сегменты, пиксели
   и списки пользователей; если нужен именно LAL-процент — это UI ads.vk.ru. Скажи об этом прямо.

Сценарий 5 (CRM-база):
1. `vk_ads_users_lists_list` — что уже загружено.
2. `vk_ads_users_lists_create(payload={"name": ..., "type": "email"|"phone"|"idfa"|"gaid"})`
3. `vk_ads_users_lists_upload_items(users_list_id=..., items=[<хэши>])`
   ⚠️ Загружаются **хэшированные** идентификаторы, не сырые email/телефоны.
4. `vk_ads_users_lists_list` — проверка размера совпадений.
```

**Сценарий 6 (правка креатива, строки 136–142).** Переписать на
`vk_ads_banners_get` / `vk_ads_content_upload_image` / `vk_ads_banners_update`, и **дописать блок про модерацию** *(M5)*:

```markdown
**Если объявление отклонено модерацией.** Повторная подача — пакетный
`vk_ads_banners_remoderate(banner_ids=[...])`. Отправить можно не любое: сначала
`vk_ads_banners_list(fields="id,user_can_request_remoderation")` и бери только те, где `true`.
В ответе поле `remoderated` показывает, принята ли заявка по каждому. Отправлять без пред-чека —
холостой вызов.
```

**Сценарий 7 (таргетинг, строки 154–159) — ключевая правка H1.** Заменить все `ad_plans` на `campaigns`:

```markdown
1. `vk_ads_campaigns_get(campaign_id=..., fields="id,name,targetings,status")` — текущий таргетинг **группы**.
2. Уточнить что добавить/убрать. Новые города — сначала `vk_ads_regions_search`, `targetings.geo.regions`
   принимает числовые id.
3. План: «Группа X. Текущая аудитория: … Добавляю Z, исключаю W. ОК?»
4. Проверить размер > 50K.
5. После «ОК» — `vk_ads_campaigns_update(campaign_id=..., payload={"targetings": {...}})`
6. `vk_ads_campaigns_get(campaign_id=...)` — проверка.
7. Лог.
```

**Сценарий 8 (строка 171).** `GET /campaigns/... /ad_plans/... /banners/... /statistics/...` →
`vk_ads_ad_plans_list` → `vk_ads_campaigns_list(fields=...)` → `vk_ads_banners_list(campaign_id=...)` →
`vk_ads_statistics_day(entity="campaigns", ids=...)`.

**Сценарий 9 (стратегия ставок, строка 194).** `GET /campaigns/{id}` → `bid_strategy` →
`vk_ads_campaigns_get(campaign_id=..., fields="id,name,budget_limit,budget_limit_day,package_id")`.
Дописать: модель оплаты зашита в `package_id` (`priced_event_type`), сменить её на лету нельзя —
нужна новая группа с другим пакетом.

**Сценарий 11 (daily check, строка 239).** → `vk_ads_statistics_day(entity="campaigns", ids=..., date_from=<вчера>, date_to=<вчера>)`.

**Сценарий 12 (соцдем, строки 271–272) — правка H2.** Заменить на:

```markdown
2. После «ОК»:
   - Новая **группа** с изменённым таргетингом: `vk_ads_campaigns_create(payload={"name": ...,
     "package_id": <тот же>, "ad_plan_id": <тот же>, "targetings": {...}, "status": "blocked",
     "banners": [ <копии креативов> ]})`
   - ⚠️ Объявления передаются **внутри** payload группы. Отдельного создания баннера нет,
     «долить» в созданную группу невозможно — группа без `banners` навсегда останется пустой
     (`issues: NO_BANNERS_WITH_ACTIVE_STATUS`) и её придётся удалять и пересоздавать.
   - ⚠️ `package_id` и `ad_plan_id` обязательны. Группа без `ad_plan_id` — «сирота», невидима в UI.
3. Старую группу не удалять — `vk_ads_campaigns_set_status(campaign_id=..., status="blocked")`.
```

**Сценарий 13 (копирование, строки 283–288) — правка H3.** Заменить на:

```markdown
1. Read: `vk_ads_ad_plans_get(ad_plan_id=...)` + `vk_ads_campaigns_list(fields="...,targetings,package_id,ad_plan_id")`
   + для каждой группы `vk_ads_banners_list(campaign_id=...)`.
2. План: «Копирую Кампанию X со всеми группами и креативами. Поменяю: <что>. Всё в `blocked`. ОК?»
3. После «ОК» — **один атомарный вызов**:
   `vk_ads_ad_plans_create(payload={"name": ..., "status": "blocked",
     "campaigns": [{..., "package_id": ..., "targetings": {...}, "banners": [{...}]}]})`
   Пошаговая альтернатива, если payload слишком большой: `vk_ads_ad_plans_create` →
   `vk_ads_campaigns_create(payload={"ad_plan_id": <новый>, ..., "banners": [...]})` по одной группе.
4. **Проверить непустой `banners`** у каждой новой группы. Пусто → залив провалился, не отдавать как успех.
5. Не активировать. Лог.
```

**Раздел «Что MCP/API VK НЕ умеет» (строки 297–305).** Убрать строку «Создать пиксель» (умеет),
добавить актуальные ограничения:

```markdown
| Не умеет | Фолбек |
|---|---|
| Создать объявление отдельным вызовом | Только вложенным массивом `banners` внутри группы |
| Долить объявления в существующую группу | Пересоздать группу целиком |
| Lookalike как отдельная сущность | Сегменты + пиксели + списки пользователей; проценты LAL — UI |
| Сменить `set_status` у `ad_plan` | `vk_ads_ad_plans_update({"status": ...})` |
| Разрезы статистики (age / region / placement) | UI ads.vk.ru |
| Асинхронные отчёты | Их нет; `statistics_summary` / `statistics_day` с явным `ids` |
| Список ранее загруженного контента | Сохраняй `id` из ответов загрузки |
| Справочники `interests`, `sectors`, `browsers`, `languages`, `os` | Отвечают 404 — не строй на них таргетинг |
| Локальные файлы в `content_upload_*` на хостовом сервере | Публичный http(s)-URL или путь B |
| Проверить, что пиксель стреляет | Браузер / UI |
| Авто-правила (если CPL > X — выключить) | Внешний скрипт на cron + API |
```

### 2.7. `references/vk-ads-mcp-integration.md` — дополнить маппинг

В таблицу «Маппинг задач скилла на инструменты» (строки 77–86) **добавить три строки**:

```markdown
| Выбор пакета размещения (Шаг 11, обязателен) | `vk_ads_packages_list(objective=...)` → `id` активного пакета. Всего ~174 пакета, фильтруй по `objective`; значения — в `available_objectives` ответа |
| Резолв гео в `region_id` (Шаги 6.5, 11) | `vk_ads_regions_search(query="<город>")` → `items[].id` в `targetings.geo.regions` |
| Повторная модерация отклонённых | `vk_ads_banners_list(fields="id,user_can_request_remoderation")` → `vk_ads_banners_remoderate(banner_ids=[...])` |
```

В раздел «Ограничения хостового режима» (строки 98–104) **добавить**:

```markdown
- **У `ad_plan` нет `set_status`.** Пауза/запуск «Кампании» целиком — только `vk_ads_ad_plans_update`
  с `{"status": ...}`. У `campaign` метод есть.
- **`package_id` обязателен при создании группы** и определяет формат, площадки и модель оплаты
  (`priced_event_type`: 0 = CPM, 1 = CPC, 30 = oCPM). После создания не меняется — другая модель
  оплаты означает новую группу.
- **В выдаче `packages_list` встречаются пакеты со `status: "blocked"`** — фильтруй по `active`.
```

### 2.8. `scripts/vk_ads_api.py` — закрыть открытый вопрос *(M8, R2)*

Пункт 3 плана 0.6.1 («Открытый вопрос — НЕ чинить вслепую») так и не закрыт, а `BannersAPI.create`
(`POST /banners.json` с `campaign_id`) остаётся несущей конструкцией пути B: `create_campaign_tree`
вызывает его в цикле. Если метода нет, путь B создаёт ровно тот же мусор, из-за которого
переписывали путь A.

**Что сделать:** ничего не переписывать вслепую. Добавить в докстринг `BannersAPI.create` пометку:

```python
    ⚠️ НЕ СВЕРЕНО. vk-ads-mcp не предоставляет отдельного создания баннера и утверждает,
    что POST /banners в API нет. Этот метод конфликтует с тем утверждением.
    Проверяется одним живым write-вызовом: создать баннер в существующей группе и сразу удалить.
    До проверки предпочитай вложенный массив `banners` внутри payload группы.
```

и завести отдельную задачу на проверочный вызов. В `vk-ads-api.md` — та же пометка рядом с описанием `/banners`.

### 2.9. `CHANGELOG.md` — запись 0.6.2

```markdown
## 0.6.2 — <дата>

Сверка с живым сервером доведена до lifecycle-режима и обвязки залива.

- **Добавлен пре-флайт залива:** `vk_ads_packages_list` (обязательный `package_id`, правила отбора
  по `status`/`priced_event_type`/`banner_format_id`) и `vk_ads_regions_search` (гео → числовые
  `region_id`). Без них атомарный `ad_plans_create` не проходил.
- **`references/lifecycle-runbook.md` переписан под правильную иерархию.** Сценарии 4, 5, 7, 12, 13
  работали в перевёрнутой модели (`ad_plan` как группа) и обращались к несуществующим ресурсам
  `/audiences.json` и `POST /banners.json`. Все 13 сценариев переведены на MCP-вызовы, REST оставлен
  как фолбек.
- **Исправлены имена и параметры:** `ad_plans_get(ad_plan_id=...)`; guardrail безопасности назван
  через реальные `campaigns_set_status` / `ad_plans_update`; зафиксировано отсутствие `set_status`
  у `ad_plan`.
- **Пиксель:** `vk_ads_remarketing_pixels_create` существует — убрано из таблицы «не умеет через API»
  в `SKILL.md` и рунбуке, Шаг 7а получил MCP-ветку.
- **Описание пути B выправлено** — порядок создания `ad_plan → campaigns → banners` приведён
  к тому, что `deploy_campaign.py` делает фактически.
- **Повторная модерация** описана с пред-чеком `user_can_request_remoderation`.
- **Открытый вопрос `POST /banners`** помечен в коде и в `vk-ads-api.md` как несверенный.
```

---

## 3. Порядок выполнения

1. **2.1, 2.2** — блокеры залива, самое дешёвое и самое ценное.
2. **2.6** — рунбук, основной объём (~200 строк правок).
3. **2.3, 2.4, 2.5, 2.7** — точечные.
4. **2.8** — пометки, без изменения поведения.
5. **2.9** — changelog, версия в `README.md` и во фронтматтере, если она там дублируется.

Всё это — правки текста и докстрингов. **Логику `scripts/*.py` в этом проходе не менять.**

---

## 4. Чеклист приёмки

```bash
# иерархия в рунбуке выправлена
grep -n "POST /ad_plans.json"        references/lifecycle-runbook.md   # → пусто
grep -n "POST /banners.json"         references/lifecycle-runbook.md   # → пусто
grep -n "ad_plans/?campaign_id"      references/lifecycle-runbook.md   # → пусто
grep -n "audiences.json"             references/lifecycle-runbook.md   # → пусто
grep -n "GET /ad_plans/{id}\` — текущий таргетинг" references/lifecycle-runbook.md  # → пусто

# блокеры закрыты
grep -rn "vk_ads_packages_list"  SKILL.md   # → минимум 1
grep -rn "vk_ads_regions_search" SKILL.md   # → минимум 2 (Шаг 6.5 и Шаг 11)

# мелкие
grep -n "ad_plans_get(id="       SKILL.md   # → пусто
grep -n "campaigns/activate"     SKILL.md   # → пусто
grep -rn "Создать пиксель (только использовать готовый)" .   # → пусто
grep -n "Создаст ad_plans (по числу аудиторий" SKILL.md      # → пусто
grep -rn "user_can_request_remoderation" references/          # → минимум 1

# не сломали то, что было верно
grep -c "vk_ads_" references/vk-ads-mcp-integration.md        # таблица 45 имён на месте
grep -rn "vk_ads_banners_create\|vk_ads_statistics_breakdown\|vk_ads_async_report" .  # → пусто
```

**Проверка живым вызовом** (read-only, безопасно) — после правок прогнать и сверить с текстом скилла:

```
vk_ads_packages_list(objective="leadads")        → 2 пакета, available_objectives из 29 значений
vk_ads_regions_search(query="Москва")            → id 5506, parent_id 70
vk_ads_campaigns_list(fields="id,name,status,objective,budget_limit,ad_plan_id,package_id,targetings,issues")
                                                 → targetings и budget на уровне campaign
```

---

## 5. Что НЕ трогать

- **Таблицу 45 инструментов** в `vk-ads-mcp-integration.md:39–49` — сверена, расхождений нет.
- **Раздел «Особенности ответов сервера»** там же — подтверждён живыми ответами.
- **`references/forecasting.md`** — верен.
- **Логику `scripts/*.py`** — только докстринг-пометка из 2.8.
- **`vk-ads-funnel/`** (старая копия под прежним именем), если она ещё существует — решение за владельцем, по умолчанию не трогать.

---

## 6. Хвост из прошлой итерации

Ad_plan `28501056` («Planfix | Лид-форма | Заявки») по-прежнему висит в кабинете: 4 группы
(`150895042`, `150895058`, `150895063` и ещё одна) с полными таргетингами, `package_id: 3215`,
`budget_limit: 6000000` — и `banners: []` у каждой. Все в `status: blocked`, все с issue
`NO_BANNERS_WITH_ACTIVE_STATUS`. Достроить их нельзя. После правок — удалить и пересоздать атомарным
вызовом. **В скоуп правок скилла не входит**, но проверять правки удобно именно на этом кейсе.

---

## Приложение А. Живые данные с сервера, 26.08.2026

**`vk_ads_packages_list(objective="leadads")`** → `count: 2`, `total_packages: 174`

| id | name | status | price | priced_event_type | banner_format_id |
|---|---|---|---|---|---|
| 3215 | `or_tt_crossdevice_video_vk_cpm_leadads` | active | 10 | 51 | 0 |
| 4618 | `or_tt_crossdevice_video_vk_cpm_leadads_svo` | active | 10 | 51 | 0 |

**`objective="site_conversions"`** → `count: 13`, среди них `3231` со `status: "blocked"`
(подтверждает необходимость фильтра по `active`), `3104`/`3217` — CPM, `3105`/`3218`/`3232` — CPC,
`3113`/`3208`/`3229`/`3858`/`4617` — oCPM.

**`available_objectives` (29)**: `appinstalls, audiolistening, branding_dzen, branding_html5,
branding_odkl, branding_premium, branding_socialengagement, branding_universal_banner, branding_video,
business_card, dzen, in_app_conversions, leadads, marketplace, max_channel, odkl, odkl_profile,
promoted_vk_post, reengagement, site_conversions, socialaudio, socialengagement,
socialengagement_profile, socialvideo, storeproductssales, survey, vk_channel, vk_miniapps, yclients`

**`vk_ads_regions_search(query="Москва")`** → `{"count": 1, "total_regions": 5561,
"items": [{"id": 5506, "name": "Москва", "parent_id": 70}]}`

**`vk_ads_campaigns_list(fields=...)`** — форма живой группы (сокращено):

```json
{
  "id": 150895042,
  "ad_plan_id": 28501056,
  "name": "S2-Лоскут · W1 интересы+ключи · H-warm-1",
  "objective": "leadads",
  "package_id": 3215,
  "status": "blocked",
  "budget_limit": 6000000,
  "budget_limit_day": null,
  "targetings": {
    "age": {"age_list": [25, …, 50], "expand": false},
    "sex": ["male", "female"],
    "geo": {"regions": [188]},
    "pads": [1011136, 1011137, …],
    "fulltime": {"mon": [0…23], …}
  },
  "issues": [
    {"code": "STOPPED", "message": "The campaign is stopped."},
    {"code": "NO_BANNERS_WITH_ACTIVE_STATUS", "message": "The campaign has no active banners."},
    {"code": "NO_MONEY", "message": "The account has no enough money."},
    {"code": "AD_PLAN_STOPPED", "message": "The ad plan is stopped."}
  ]
}
```

Конверт ответа: `{count, items, offset}`.

Это и есть доказательство, что `targetings`, `budget_limit`, `objective` и `package_id` живут
на `campaign`, а не на `ad_plan` — то есть рунбук в 0.6.1 описывал не ту сущность.
