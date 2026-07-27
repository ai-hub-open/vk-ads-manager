# Credentials — управление API-ключами в скилле

Универсальная система хранения и доступа к API-ключам для всех скриптов скилла. Введена в M1, используется во всех будущих модулях (M2 видео, M3 PDF, M4 VK Ads залив и т.д.).

## Где хранятся

`~/.vk-ads-manager/credentials.json` — пользовательская папка, **вне** рабочей папки проекта. Не попадает в git проекта, не уходит вместе с архивом кампании клиенту.

На Unix: права файла 0600 (только владелец). На Windows: стандартные ACL пользователя.

## Поддерживаемые сервисы

| Сервис | Назначение | Env-переменная (опц.) | Где взять |
|---|---|---|---|
| `openai` | DALL-E / GPT-image-1 / ChatGPT | `OPENAI_API_KEY` | https://platform.openai.com/api-keys |
| `vk_ads` | VK Ads API (управление кампаниями) | `VK_ADS_ACCESS_TOKEN` | ads.vk.ru → API |
| `runway` | Runway ML (видео M2) | `RUNWAY_API_KEY` | https://app.runwayml.com/account |
| `kling` | Kling AI (видео M2) | `KLING_API_KEY` | https://klingai.com → developer |
| `aismm` | AiSMM Pro / Контент Машина | `AISMM_API_KEY` | aismm.pro → ЛК |
| `anthropic` | Anthropic API | `ANTHROPIC_API_KEY` | https://console.anthropic.com/settings/keys |

Добавление нового сервиса = одна запись в `SERVICE_REGISTRY` в `scripts/credentials.py`.

## Как скрипты их используют

В коде:

```python
from scripts.credentials import load_api_key, CredentialNotFound

try:
    api_key = load_api_key("openai")
except CredentialNotFound as e:
    print(e)  # выдаёт инструкцию: "запусти python -m scripts.manage_credentials set openai"
    sys.exit(1)
```

Порядок поиска:
1. Env-переменная (`OPENAI_API_KEY` и т.п.) — если задана, берём её
2. `~/.vk-ads-manager/credentials.json` — следующий fallback
3. Иначе `CredentialNotFound` с понятной инструкцией

## CLI для пользователя

```bash
# Сохранить ключ (ввод скрыт, не попадает в shell history)
python -m scripts.manage_credentials set openai
# → "Введите ключ для 'openai' (ввод скрыт): ..."

# Сохранить через аргумент (НЕ безопасно — попадает в history)
python -m scripts.manage_credentials set openai --key sk-...

# Список сохранённых
python -m scripts.manage_credentials list

# Проверить что ключ сохранён (показывает маску, не сам ключ)
python -m scripts.manage_credentials get openai
# → "openai: sk-1...AB12"

# Удалить
python -m scripts.manage_credentials delete openai

# Инфо о сервисе (с инструкцией откуда взять ключ)
python -m scripts.manage_credentials info openai
```

## Когда Claude должен запросить ключ

Скилл рассчитан на **Cowork-first** работу — пользователь не открывает терминал. Поэтому **Способ Б — дефолт.**

В SKILL.md в каждом шаге, требующем ключ, есть инструкция:

```
Перед запуском <script_name>:
1. Проверь — выполни сам через bash:
   python -m scripts.manage_credentials list
2. Если <service> в списке — ключ уже есть, продолжай.
3. Если нет — попроси пользователя скинуть ключ в чат:
   «Скинь сюда ключ <service>. Я сохраню в безопасном месте, спрашивать больше не буду.
   Если ключа нет — заведи на <how_to_get>».
4. Когда пользователь пришлёт ключ — сразу выполни через bash:
   python -m scripts.manage_credentials set <service> --key <KEY>
5. Подтверди «✓ сохранён» и продолжай работать.
```

## Паттерны работы с ключами

### Способ Б (ДЕФОЛТ для Cowork-first)

Пользователь скидывает ключ в чат → Claude сохраняет через `--key`.

- ✅ Не требует от пользователя терминальных навыков
- ✅ Работает в одном окне Cowork
- ⚠️ Ключ попадает в историю чата Cowork — но не в артефакты проекта

Применять когда: пользователь не-технический, работает только через Cowork.

### Способ А (для технических пользователей)

Пользователь сам выполняет `python -m scripts.manage_credentials set <service>` — getpass запрашивает ключ.

- ✅ Самый безопасный: ключ не светится в чате и не попадает в shell history
- ❌ Требует от пользователя терминальных навыков

Применять когда: пользователь явно говорит «я сам введу в терминале» или работает с особо чувствительными ключами (production VK Ads).

### Env-переменная (для CI / shared систем)

`OPENAI_API_KEY=sk-... python ...` — для CI/CD пайплайнов.

- ✅ Стандартный путь для DevOps
- ✅ Никогда не пишется на диск
- ❌ Нужно переустанавливать в каждом shell session

## Запрещённые паттерны

❌ **Никогда не сохраняй ключ в**:
- `_state.json` рабочей папки (попадает в архив клиента)
- `creatives.json` или других артефактах кампании
- `operations_log.md` (даже как «использован openai key xxx»)

❌ **Никогда не печатай ключ полностью в чате после сохранения** — только маска через `manage_credentials get`.

❌ **Никогда не передавай ключ в Git** — `.gitignore` должен включать `~/.vk-ads-manager/` и любые локальные `.env` файлы.

## Расширение реестра

Чтобы добавить новый сервис (например, Imagen 3 от Google):

1. В `scripts/credentials.py` в `SERVICE_REGISTRY`:
   ```python
   "google_genai": {
       "env": "GOOGLE_GENAI_API_KEY",
       "description": "Google AI Studio (Gemini, Imagen)",
       "how_to_get": "https://aistudio.google.com/apikey",
       "format_hint": "AIza...",
   },
   ```

2. В коде, который использует:
   ```python
   from scripts.credentials import load_api_key
   key = load_api_key("google_genai")
   ```

3. В соответствующем `references/<module>.md` добавить упоминание.

Готово — пользователь может выполнить `manage_credentials set google_genai` и сразу использовать.

## Проблемы и решения

**Q: Я установил env-переменную, но скрипт говорит «не найдено».**
A: Проверь что переменная установлена в **этом** shell-сессии (`echo $OPENAI_API_KEY` / `echo %OPENAI_API_KEY%`). На Windows возможно нужен перезапуск терминала после `setx`.

**Q: Я хочу разные ключи для разных проектов.**
A: Сейчас один global credentials.json. Если нужно — используй env-переменную локально для одного проекта (env > global file).

**Q: Я переношу проект на другой компьютер — что брать?**
A: Только рабочую папку (`vk-campaign-<slug>/`). Credentials остаются на старой машине и устанавливаются заново на новой через `manage_credentials set`.

**Q: А если CI / shared машина?**
A: Используй env-переменные через secrets вашего CI (GitHub Actions secrets / GitLab CI variables и т.д.). credentials.json только для локальной разработки.
