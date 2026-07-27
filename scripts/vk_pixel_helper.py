#!/usr/bin/env python3
"""
vk_pixel_helper.py — генерирует код пикселя VK Ads + чек-лист событий
+ HTML-страничку для проверки что пиксель установлен и стреляет.

Usage:
    python -m scripts.vk_pixel_helper --pixel-id VK-RTRG-XXXXXX --workspace <path>

Артефакты:
- vk_pixel_snippet.html — JS-сниппет для вставки в <head>
- vk_pixel_events.md — чек-лист событий + как настроить
- vk_pixel_check.html — страница для отладки (проверить что события стреляют)
"""

import argparse
import sys
from pathlib import Path


PIXEL_TEMPLATE = """<!-- Top.Mail.Ru / VK Ads pixel -->
<script type="text/javascript">
(function(d, w, id) {{
  if (d.getElementById(id)) return;
  var ts = d.createElement('script');
  ts.type = 'text/javascript';
  ts.async = true;
  ts.id = id;
  ts.src = 'https://top-fwz1.mail.ru/js/code.js';
  var f = function () {{
    var s = d.getElementsByTagName('script')[0];
    s.parentNode.insertBefore(ts, s);
  }};
  if (w.opera == '[object Opera]') {{
    d.addEventListener('DOMContentLoaded', f, false);
  }} else {{
    f();
  }}
}})(document, window, "tmr-code");
</script>
<script type="text/javascript">
var _tmr = window._tmr || (window._tmr = []);
_tmr.push({{ id: "{pixel_id}", type: "pageView", start: (new Date()).getTime() }});
</script>
<noscript>
  <div><img src="https://top-fwz1.mail.ru/counter?id={pixel_id};js=na" style="position:absolute;left:-9999px;" alt="Top.Mail.Ru" /></div>
</noscript>
<!-- /Top.Mail.Ru / VK Ads pixel -->
"""

EVENT_TEMPLATE = """// Событие: {name}
// Когда вызывать: {when}
_tmr.push({{ type: 'reachGoal', id: '{pixel_id}', goal: '{name}'{params} }});
"""

EVENTS_CHECKLIST = """# Пиксель VK Ads — чек-лист событий

Pixel ID: `{pixel_id}`

## Куда вставить

1. Скопируй содержимое `vk_pixel_snippet.html`
2. Вставь в `<head>` ВСЕХ страниц сайта (через GTM или прямо в код)
3. Проверь что в DevTools → Network есть запросы к `top-fwz1.mail.ru/counter` при загрузке страницы

## Базовые события (минимум)

| Событие | Когда стреляет | Назначение |
|---|---|---|
| `pageView` | Автоматически при загрузке | Базовая статистика, ретаргетинг по визитам |
| `lead` | Отправка формы заявки | Оптимизация на лиды, LAL по заявкам |
| `purchase` | Успешная оплата | Оптимизация на продажи, LAL по покупателям |
| `registration` | Регистрация пользователя | Для SaaS — целевое действие |
| `add_to_cart` | Добавление в корзину | Для e-commerce ретаргетинга |
| `view_content` | Просмотр карточки товара / страницы продукта | Микро-конверсия для тёплой аудитории |

## JS-код событий

Вставь нужные события в соответствующих местах:

```javascript
// При успешной отправке формы заявки
_tmr.push({{ type: 'reachGoal', id: '{pixel_id}', goal: 'lead' }});

// При успешной оплате (можно передать value)
_tmr.push({{
  type: 'reachGoal',
  id: '{pixel_id}',
  goal: 'purchase',
  params: {{ value: 5000, product_id: 'starter' }}
}});

// При регистрации
_tmr.push({{ type: 'reachGoal', id: '{pixel_id}', goal: 'registration' }});

// При добавлении в корзину
_tmr.push({{
  type: 'reachGoal',
  id: '{pixel_id}',
  goal: 'add_to_cart',
  params: {{ value: 5000 }}
}});
```

## Проверка работы

1. Открой страницу с пикселем в DevTools (F12) → Network
2. Найди запросы к `top-fwz1.mail.ru/counter` — должны быть с `id={pixel_id}`
3. Открой `vk_pixel_check.html` в браузере — там визуальный лог событий
4. В кабинете ads.vk.ru → Сайты → твой сайт → проверь активность за последний час

## Маппинг событий на цели в кабинете VK Реклама

1. Зайди в ads.vk.ru → Цели
2. Создай цели для каждого нужного события:
   - Название цели = `lead`
   - Тип = «Событие пикселя»
   - Условие: пиксель с id `{pixel_id}`, событие `lead`
3. После создания цели — она доступна для выбора при настройке кампании
4. Подожди 1-2 часа чтобы накопились первые события (только после этого алгоритм начнёт оптимизироваться)

## Если события не стреляют

- Проверь что Pixel ID в коде совпадает с ID в кабинете
- Проверь что код в `<head>`, не в `<body>`
- Проверь нет ли блокировщика рекламы (uBlock и др. часто режут)
- Используй Conversion API для серверной передачи (для приватности)
"""

CHECK_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>VK Pixel Debug — {pixel_id}</title>
<style>
body {{ font-family: sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; }}
button {{ padding: 10px 20px; margin: 5px; font-size: 14px; cursor: pointer; }}
.log {{ background: #f5f5f5; padding: 15px; border-radius: 4px; min-height: 200px; margin-top: 20px; font-family: monospace; font-size: 12px; }}
.ok {{ color: green; }}
.err {{ color: red; }}
</style>
</head>
<body>
<h1>VK Pixel Debug</h1>
<p>Pixel ID: <strong>{pixel_id}</strong></p>
<p>Эта страница установит пиксель и позволит вручную выстрелить тестовыми событиями. Если запросы к <code>top-fwz1.mail.ru</code> уходят (проверь Network в DevTools) — пиксель работает.</p>

<h2>Тестовые события</h2>
<button onclick="testEvent('lead')">Выстрелить «lead»</button>
<button onclick="testEvent('purchase')">Выстрелить «purchase»</button>
<button onclick="testEvent('registration')">Выстрелить «registration»</button>
<button onclick="testEvent('add_to_cart')">Выстрелить «add_to_cart»</button>

<div class="log" id="log"></div>

<!-- Pixel -->
<script type="text/javascript">
(function(d, w, id) {{
  if (d.getElementById(id)) return;
  var ts = d.createElement('script');
  ts.type = 'text/javascript';
  ts.async = true;
  ts.id = id;
  ts.src = 'https://top-fwz1.mail.ru/js/code.js';
  var f = function () {{
    var s = d.getElementsByTagName('script')[0];
    s.parentNode.insertBefore(ts, s);
  }};
  if (w.opera == '[object Opera]') {{
    d.addEventListener('DOMContentLoaded', f, false);
  }} else {{
    f();
  }}
}})(document, window, "tmr-code");
</script>
<script type="text/javascript">
var _tmr = window._tmr || (window._tmr = []);
_tmr.push({{ id: "{pixel_id}", type: "pageView", start: (new Date()).getTime() }});

function log(msg, cls) {{
  var el = document.getElementById('log');
  var line = document.createElement('div');
  line.className = cls || '';
  line.textContent = new Date().toISOString().split('T')[1].split('.')[0] + ' — ' + msg;
  el.insertBefore(line, el.firstChild);
}}

function testEvent(name) {{
  _tmr.push({{ type: 'reachGoal', id: '{pixel_id}', goal: name }});
  log('Sent event: ' + name + ' (проверь Network → запросы к top-fwz1.mail.ru/counter)', 'ok');
}}

log('Pixel инициализирован, pageView отправлен. Проверь Network.', 'ok');
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Генератор пикселя VK Ads")
    parser.add_argument(
        "--pixel-id",
        required=True,
        help="ID пикселя из кабинета (например VK-RTRG-1234567)",
    )
    parser.add_argument("--workspace", required=True, help="Папка vk-campaign-<slug>/")
    args = parser.parse_args()

    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    # 1. Pixel snippet
    snippet_path = workspace / "vk_pixel_snippet.html"
    snippet_path.write_text(
        PIXEL_TEMPLATE.format(pixel_id=args.pixel_id), encoding="utf-8"
    )

    # 2. Events checklist
    checklist_path = workspace / "vk_pixel_events.md"
    checklist_path.write_text(
        EVENTS_CHECKLIST.format(pixel_id=args.pixel_id), encoding="utf-8"
    )

    # 3. Debug page
    check_path = workspace / "vk_pixel_check.html"
    check_path.write_text(
        CHECK_PAGE_TEMPLATE.format(pixel_id=args.pixel_id), encoding="utf-8"
    )

    print(f"OK: создано 3 файла в {workspace}")
    print(f"  - {snippet_path.name}")
    print(f"  - {checklist_path.name}")
    print(f"  - {check_path.name}")
    print(f"\nСледующий шаг: вставить содержимое {snippet_path.name} в <head> сайта.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
