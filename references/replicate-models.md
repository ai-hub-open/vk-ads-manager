# Каталог Replicate моделей для генерации видео

Replicate — хаб с десятками видео-моделей под одним API. Используется через провайдер `replicate` с параметром `--model <slug>`.

## Где смотреть актуальный каталог

Полный список: https://replicate.com/explore (фильтр «video»)

Топ-листы:
- https://replicate.com/collections/text-to-video
- https://replicate.com/collections/image-to-video

Каждая модель имеет страницу с примерами, ценой и точной сигнатурой input. Перед использованием новой модели — глянь её page чтобы понять какие параметры она принимает.

## Поддерживаемые модели (с готовыми input-маппингами)

Скилл умеет автоматически маппить универсальные параметры (prompt / duration / aspect_ratio / seed_image) на input для следующих моделей. Если модель не в этом списке — будет использован дефолтный маппинг (с риском неточностей в параметрах).

### Kling

| Slug | Длительность | Цена ориент. | Сильная сторона |
|---|---|---|---|
| `kwaivgi/kling-v1.6-standard` | 5/10s | ~$0.05/sec | Доступный, стабильный |
| `kwaivgi/kling-v1.6-pro` | 5/10s | ~$0.10/sec | Лучше качество, дороже |
| `kwaivgi/kling-v2-master` | 5/10s | ~$0.28/sec | Текущий флагман Kling, хорошо для UGC-style |

**Ключевая особенность Kling:** хорошие переходы при image-to-video → лучший выбор для `--chain-segments`.

### Bytedance Seedance

| Slug | Длительность | Цена ориент. | Сильная сторона |
|---|---|---|---|
| `bytedance/seedance-1-pro` | 5/10s | ~$0.15/sec | Высокое качество motion |
| `bytedance/seedance-1-lite` | 5/10s | ~$0.04/sec | Дёшево, для теста |

### Tencent Hunyuan

| Slug | Длительность | Цена ориент. | Сильная сторона |
|---|---|---|---|
| `tencent/hunyuan-video` | 2-6s (через num_frames) | ~$0.20/sec | Open-source модель в облаке |

### Wan Video

| Slug | Длительность | Цена ориент. | Сильная сторона |
|---|---|---|---|
| `wavespeedai/wan-2.1-i2v-720p` | до 5s | ~$0.02-0.04/sec | Дешёвая image-to-video, быстрая |

### Minimax / Hailuo

| Slug | Длительность | Цена ориент. | Сильная сторона |
|---|---|---|---|
| `minimax/video-01` | 6s (фикс) | ~$0.50/clip | Очень реалистичные лица для UGC |
| `minimax/hailuo-02` | 6/10s | ~$0.45-0.75/clip | Hailuo 2 — лучшая physics simulation |

### Google Veo (если доступно через Replicate)

| Slug | Длительность | Цена ориент. | Сильная сторона |
|---|---|---|---|
| `google/veo-3` | 5-8s | ~$0.50/sec | Премиум SOTA, есть audio |

## Как использовать новую модель (которой нет в списке)

1. Зайди на страницу модели на replicate.com
2. Возьми slug (формат `owner/name`)
3. Запусти:
   ```bash
   python -m scripts.generate_creative_videos \
     --provider replicate \
     --model owner/new-model-name \
     --workspace <path> \
     --only cr_pain_p1_video
   ```
4. Если получится — модель работает с дефолтным маппингом (prompt, duration, aspect_ratio, seed_image как стандартные имена).
5. Если ошибка типа `unexpected input parameter` — посмотри на странице модели какие точно поля она ждёт, и добавь маппинг в `scripts/video_providers/replicate.py:MODEL_INPUT_SCHEMAS`.

## Версия модели vs slug

Replicate принимает 2 формата идентификатора:

**Slug (рекомендуется):** `owner/name` — всегда последняя версия. Удобно для тестирования.
```
--model kwaivgi/kling-v1.6-standard
```

**Version hash:** конкретная замороженная версия, не меняется со временем. Используй для production-стабильности.
```
--model kwaivgi/kling-v1.6-standard:abc123def456...
```

Hash берётся со страницы модели → «Use as production» или через API.

## Стратегия выбора модели под задачу

| Тип креатива | Рекомендуемые модели |
|---|---|
| Pain reframe (split-screen, без людей) | `kwaivgi/kling-v1.6-standard` (универсал) |
| UGC с реальным человеком | `minimax/hailuo-02` (лучшие лица) или `kwaivgi/kling-v2-master` |
| Product demo (UI скринкаст) | `bytedance/seedance-1-pro` (стабильное motion) |
| Брендовая абстрактная анимация | `bytedance/seedance-1-lite` (дёшево, для перебора вариантов) |
| Премиум финал | `google/veo-3` (если в бюджете) |

## A/B тестирование моделей

Хороший паттерн: на этапе разработки кампании сгенерь 3 варианта одного промпта через **разные модели**:

```bash
# Вариант 1: Kling
python -m scripts.generate_creative_videos \
  --only cr_pain_p1_video --variants 1 \
  --provider replicate --model kwaivgi/kling-v1.6-standard
mv assets/videos/cr_pain_p1_video_seg1_var1.mp4 assets/videos/cr_pain_p1_video_kling.mp4

# Вариант 2: Seedance Pro
python -m scripts.generate_creative_videos \
  --only cr_pain_p1_video --variants 1 \
  --provider replicate --model bytedance/seedance-1-pro
mv assets/videos/cr_pain_p1_video_seg1_var1.mp4 assets/videos/cr_pain_p1_video_seedance.mp4

# Сравни — какой больше нравится → продолжай с ним
```

## Сравнение стоимости (15s видео = 3 сегмента по 5s)

| Модель | Цена 5s | Цена 15s |
|---|---|---|
| `wavespeedai/wan-2.1-i2v-720p` | $0.20 | $0.60 |
| `bytedance/seedance-1-lite` | $0.20 | $0.60 |
| `kwaivgi/kling-v1.6-standard` | $0.25 | $0.75 |
| `bytedance/seedance-1-pro` | $0.75 | $2.25 |
| `kwaivgi/kling-v1.6-pro` | $0.50 | $1.50 |
| `kwaivgi/kling-v2-master` | $1.40 | $4.20 |
| `google/veo-3` | $2.50 | $7.50 |

Цены ориентировочные на 2026 май — реальные смотри на странице модели.

## Расширение MODEL_INPUT_SCHEMAS

Если хочешь использовать модель с особыми параметрами, отредактируй `scripts/video_providers/replicate.py`:

```python
MODEL_INPUT_SCHEMAS = {
    ...
    "owner/new-model": {
        "prompt": "prompt",                       # как маппится prompt
        "duration": ("duration_sec", lambda d: d), # с трансформацией
        "aspect_ratio": "ratio",
        "seed_image": "init_image",
        "extra": {                                # дополнительные параметры
            "fps": 24,
            "quality": "hd",
        },
    },
}
```

Формат:
- Ключ → имя поля в модели Replicate
- Значение строкой → прямое соответствие
- Значение кортежем `(field_name, transform_fn)` → с преобразованием
- `extra` → словарь фиксированных параметров (всегда добавляется)
