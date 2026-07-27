# VK Ads — генерация видео через AI (M2)

Модуль M2 — генератор видео для креативов через провайдер-абстракцию. Сейчас полностью реализован Runway (Gen-4 Turbo), остальные провайдеры (Kling, AiSMM, Veo) — заглушки.

## Когда использовать

- После Шагов 10 + 10.5 (есть `creatives.json` с видео-креативами и опционально картинки в `assets/images/`).
- Перед заливом (Шаг 11).
- Когда хочется AI-видео без съёмок (pain reframe, demos) или нет времени снимать UGC.

## Когда НЕ использовать

- **Реальные UGC-видео с настоящими людьми и эмоциями** — AI пока «искусственный». Лучше попросить клиента / снять самим.
- **Длинные видео > 15 секунд** — провайдеры выдают 5-10s блоками, склейка нужна руками (TODO: автосклейка через FFmpeg).
- **Видео с конкретной типографикой / логотипами** — текст в AI-видео часто плывёт. Лучше дать чистое видео + добавить overlay в DaVinci / Premiere / CapCut.

## Архитектура

```
scripts/
├── generate_creative_videos.py    ← главный CLI
├── video_prompt_templates.py       ← 3 типа сценариев
└── video_providers/
    ├── __init__.py                 ← factory get_provider()
    ├── base.py                     ← BaseVideoProvider
    ├── runway.py                   ✅ полная реализация (дефолт)
    ├── kling.py                    ⏳ заглушка
    ├── aismm.py                    ⏳ заглушка
    └── veo.py                      ⏳ заглушка (Google AI Studio)
```

## 3 типа видео-сценариев

| Тип | Когда | Длина MVP |
|---|---|---|
| `pain_reframe` | Split-screen «до/после», без людей | 5s |
| `ugc_testimonial` | AI-имитация реального человека на камеру | 5s |
| `product_demo` | Скринкаст UI с курсором | 5s |

Автоопределение типа в `detect_video_type()`:
- `name` содержит `pain` или `script` содержит «before/after» → `pain_reframe`
- `name` содержит `ugc` или `script` содержит «real person» → `ugc_testimonial`
- `name` содержит `demo` или `image_or_video` содержит «screencast/UI» → `product_demo`

## Провайдеры

### Replicate (✅ рекомендуется для гибкости и тестирования)

- API: https://replicate.com — хаб для сотен моделей
- Один ключ → доступ ко всем моделям: Kling (v1.5/v1.6/v2), Hunyuan, Seedance, Wan, Hailuo, Veo через Replicate, и др.
- Модель указывается через `--model <slug>` (например `kwaivgi/kling-v1.6-standard`)
- Workflow: POST `/v1/models/<slug>/predictions` → polling → output URL
- Ключ: `replicate` в credentials, env `REPLICATE_API_TOKEN`
- **Когда выбирать:** для гибкости (быстрая смена SOTA-моделей), A/B сравнения моделей, тестирования новинок
- Подробный каталог: см. `references/replicate-models.md`

### Runway (✅ полная реализация)

- API: https://docs.dev.runwayml.com/
- Модели: `gen4_turbo` (дешевле, быстрее), `gen4` (качественнее)
- Длительности: **5 или 10 секунд**
- Aspect ratios: 16:9, 9:16, 4:3, 3:4, 1:1
- Цена: ~$0.05/sec (gen4_turbo) → 5s = $0.25, 10s = $0.50
- Workflow: POST → task_id → polling каждые 5s до SUCCEEDED → download
- Ключ: `runway` в credentials, env `RUNWAY_API_KEY`

### Kling (✅ полная реализация)

- API: https://api.klingai.com/v1/videos/
- Модели: `kling-v1`, `kling-v1-5`, `kling-v1-6` (дефолт), `kling-v2-master`
- Aspect ratios: 16:9, 9:16, 1:1
- Длительности: 5 или 10 сек
- Цена: ~$0.10/sec для v1.6 standard
- **Особенность auth:** AccessKey + SecretKey → JWT (HS256) → Bearer. В credentials хранится одной строкой `AccessKey:SecretKey`.
- Ключ: `kling` в credentials

### Veo (✅ полная реализация)

- API: Google AI Studio (https://ai.google.dev/gemini-api/docs/video)
- Endpoint: `generativelanguage.googleapis.com/v1beta/models/{model}:predictLongRunning`
- Модели: `veo-3.0-generate-001` (дефолт стабильный), `veo-3.1-generate-preview`, `veo-fast-generate`
- Длительности: 4-8 сек
- Aspect ratios: 16:9, 9:16 (1:1 не нативно)
- Цена: ~$0.50/sec (премиум SOTA)
- Workflow: POST `predictLongRunning` → operation name → GET polling → URI или base64
- Ключ: `google_genai` в credentials (формат `AIza...`), env `GOOGLE_GENAI_API_KEY`

### AiSMM Pro / Контент Машина (✅ generic-реализация)

- Для владельцев AiSMM Pro — внутренний продукт (Veo 3.1 + Kling 2.6 под капотом)
- Generic REST-pattern: POST → task_id → polling → download
- Длительности: 5/10/15/30 сек
- Если у AiSMM реальный API отличается — adjustment в `aismm.py:generate()`
- Ключ: `aismm` в credentials, env `AISMM_API_KEY`
- Опционально: env `AISMM_API_BASE` для кастомного endpoint

## Workflow

### Шаг 1. Dry-run (бесплатно)

Claude выполняет сам через bash:

```bash
python -m scripts.generate_creative_videos --workspace <path> --dry-run
```

Результат: для каждого видео-креатива в `assets/video_prompts/<name>.txt` — готовый промпт. Прочитай 1-2 — оцени.

### Шаг 2. Проверка ключа

Claude проверяет:

```bash
python -m scripts.manage_credentials list
```

Если `runway` в списке — продолжаем. Если нет — Claude просит пользователя:

> Для видео нужен ключ Runway. Скинь сюда — я сохраню. Если ключа нет: app.runwayml.com/account → Settings → API.

Когда пользователь пришлёт ключ → Claude сохраняет:

```bash
python -m scripts.manage_credentials set runway --key <key>
```

### Шаг 3. Тест на одном видео

```bash
python -m scripts.generate_creative_videos --workspace <path> --only cr_pain_p1_video
```

~$0.25-0.50 за тест.

### Шаг 4. Полный прогон

```bash
python -m scripts.generate_creative_videos --workspace <path>
```

Можно с seed-картинками для image-to-video (стабильность стартового кадра):

```bash
python -m scripts.generate_creative_videos --workspace <path> --use-seed-images
```

Использует `assets/images/<name>.png` (если уже сгенерены через M1) как первый кадр видео — последующее движение «оживляет» картинку.

## Промпт-инженерия для видео

### Что работает

- **Описывать movement, camera moves, transitions** — «slowly pushes in», «wipe to right»
- **Указывать lighting / mood** — «soft natural lighting», «cinematic»
- **Чёткие сегменты по секундам** — «0-2s: ..., 2-3s: transition, 3-5s: ...»
- **«No text overlay»** — если хотим чистое видео для добавления текста в Premiere

### Что НЕ работает

- Точное движение губ для UGC (lip-sync) — модели плохо синхронизируют
- Текст на экране — буквы плывут, читаемость низкая
- Сложные пайплайны действий > 5 секунд — теряется консистентность
- Конкретные продукты по имени — модели стараются избегать копирайта

## A/B Variants workflow (генерируем несколько → выбираем лучший → финализируем)

Видео-генерация — лотерея. Один и тот же промпт даёт разные результаты. Чтобы не пугаться первого посредственного результата, **сначала генерим N вариантов первого сегмента**, выбираем лучший, дозагенериваем остальные с ним как seed → склейка.

### Этап 1: variants

```bash
python -m scripts.generate_creative_videos \
  --workspace <path> \
  --only cr_pain_p1_video \
  --provider replicate \
  --model kwaivgi/kling-v1.6-standard \
  --variants 3
```

Создаёт `assets/videos/cr_pain_p1_video_seg1_var1.mp4`, `_var2.mp4`, `_var3.mp4`.

После этого:
- Claude показывает 3 видео через computer:// ссылки в чате
- Пользователь говорит «вариант 2 лучший»
- Claude переходит к Этапу 2

### Этап 2: финализация с выбранным вариантом

```bash
python -m scripts.generate_creative_videos \
  --workspace <path> \
  --only cr_pain_p1_video \
  --provider replicate \
  --model kwaivgi/kling-v1.6-standard \
  --chosen-variant 2 \
  --target-duration 15 \
  --chain-segments
```

Что произойдёт:
1. `cr_pain_p1_video_seg1_var2.mp4` копируется как `_seg1.mp4`
2. Извлекается последний кадр seg1 через ffmpeg
3. Генерируется `_seg2.mp4` с этим кадром как seed (для плавного перехода)
4. Извлекается последний кадр seg2 → seed для seg3
5. `_seg3.mp4` генерируется
6. `concat_videos([seg1, seg2, seg3])` → финальный `cr_pain_p1_video.mp4` (15s)

### Стратегия variants по типу креатива

- **UGC видео:** generate 4-5 вариантов через `minimax/hailuo-02` → лица очень разные, выбираем самый аутентичный
- **Pain reframe:** 2-3 вариантов достаточно, разница меньше
- **Product demo:** 1-2 варианта + сразу финализация (UI screenshots более детерминированы)

### A/B сравнение моделей

Можно генерировать варианты через **разные модели** для одного креатива — сравнить какая модель лучше под этот промпт:

```bash
# Прогнать одну модель
python -m scripts.generate_creative_videos --only X --variants 1 \
  --provider replicate --model kwaivgi/kling-v1.6-standard

# Переименовать чтобы не перезатёрся
mv assets/videos/X_seg1_var1.mp4 assets/videos/X_kling.mp4

# Прогнать другую модель
python -m scripts.generate_creative_videos --only X --variants 1 \
  --provider replicate --model bytedance/seedance-1-pro
mv assets/videos/X_seg1_var1.mp4 assets/videos/X_seedance.mp4

# Сравнить вручную → продолжать с победителем
```

## Длинные видео — FFmpeg склейка (новое)

Большинство провайдеров отдают 5-10 секундные сегменты. Чтобы получить 15-30 секундное видео для VK Реклама — генерим **N сегментов и склеиваем через FFmpeg**.

### Workflow

```bash
# Запросить 15-секундное видео (3 сегмента по 5s, склейка авто)
python -m scripts.generate_creative_videos \
  --workspace <path> \
  --target-duration 15

# С chain-segments — последний кадр сегмента N становится seed для N+1
# (более плавные переходы)
python -m scripts.generate_creative_videos \
  --workspace <path> \
  --target-duration 30 \
  --chain-segments
```

### Что происходит под капотом

1. Скрипт считает: `num_segments = target_duration / max_duration` (округление вверх)
2. Генерирует каждый сегмент через провайдер → сохраняет в `assets/videos/<name>_seg<N>.mp4`
3. Если `--chain-segments`: извлекает последний кадр через ffmpeg → передаёт как `seed_image` следующему сегменту
4. Склеивает все сегменты через `ffmpeg -f concat` → финальный `<name>.mp4`
5. Промежуточные сегменты остаются в `assets/videos/` для отладки

### FFmpeg-операции (scripts/video_concat.py)

Отдельный модуль с CLI:

```bash
# Проверить установлен ли ffmpeg
python -m scripts.video_concat check

# Склеить вручную
python -m scripts.video_concat concat --input s1.mp4 s2.mp4 s3.mp4 --output final.mp4

# Извлечь последний кадр (для chain)
python -m scripts.video_concat last-frame --input video.mp4 --output last.png

# Добавить overlay-текст
python -m scripts.video_concat overlay-text \
  --input video.mp4 --output result.mp4 \
  --text "5 готовых процессов" --position bottom
```

### Требование: FFmpeg

- Windows: https://www.gyan.dev/ffmpeg/builds/ → распаковать → добавить в PATH
- macOS: `brew install ffmpeg`
- Linux: `sudo apt install ffmpeg`

Если ffmpeg не установлен, скрипт работает только в режиме 1 сегмента (без `--target-duration` или `--chain-segments`).

## Ограничения

- **Без звука** — большинство провайдеров не генерят аудио (Runway, Kling). Veo экспериментально умеет. Озвучку добавляем отдельно (ElevenLabs / Suno / реальный диктор).
- **Без overlay-текста по умолчанию** — добавляется через `scripts/video_concat.py overlay-text` или в постпродакшене.
- **Chain-segments** — экспериментально, качество переходов зависит от провайдера (Kling хорошо, Runway средне).
- **Sound при склейке через concat** — все сегменты должны иметь одинаковый аудио-формат (или вообще без аудио). Если разные — используется `reencode` fallback.

## Roadmap для модуля

- [ ] FFmpeg-склейка нескольких 5s-сегментов в одно 15-30s видео
- [ ] Интеграция Kling (для тех у кого есть подписка)
- [ ] Интеграция Veo через Google AI Studio (премиум качество)
- [ ] Интеграция AiSMM Pro (для владельцев продукта)
- [ ] Auto-добавление overlay-текста через FFmpeg + subtitles
- [ ] Audio generation через ElevenLabs API для UGC voiceover

## Чек-лист готовности M2 для запуска

- [ ] `runway` ключ в credentials (или другой провайдер)
- [ ] `pip install requests` (для polling)
- [ ] Dry-run прошёл — промпты выглядят разумно
- [ ] Тест на 1 видео — результат устраивает
- [ ] Полный прогон — все видео сгенерены в `assets/videos/`
- [ ] Ручной ревью качества
- [ ] (опционально) добавлен overlay-текст / звук в постпродакшене
- [ ] Видео готовы к заливу через M4
