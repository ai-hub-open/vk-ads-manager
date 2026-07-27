"""
credentials.py — универсальная система хранения и чтения API-ключей для скилла.

Используется всеми скриптами скилла (generate_creative_images, generate_videos,
vk_ads_api, и т.д.) для получения ключей без жёсткой привязки к env-переменным.

Стратегия поиска ключа:
1. Если задана env-переменная (например OPENAI_API_KEY) — берём её.
2. Иначе читаем из ~/.vk-ads-manager/credentials.json
3. Если и там нет — raises CredentialNotFound с инструкцией.

Файл credentials.json создаётся при первом вызове `set_api_key()` или через
CLI `python -m scripts.manage_credentials set <service>`.

Поддерживаемые сервисы (`SERVICE_REGISTRY`):
- openai     — OpenAI API (DALL-E, GPT-image, ChatGPT)
- vk_ads     — VK Ads API (token для управления кампаниями)
- runway     — Runway ML (видео-генерация, M2)
- kling      — Kling AI (видео-генерация, M2)
- aismm      — AiSMM Pro / Контент Машина (если есть API)
- anthropic  — Anthropic API (для будущих модулей)

Безопасность:
- Файл credentials.json сохраняется с правами 600 (только владелец) на Unix.
- На Windows используются стандартные ACL пользователя.
- Файл лежит вне рабочей папки проекта — не попадёт в git/share.
"""

import json
import os
import stat
import sys
from pathlib import Path


class CredentialNotFound(Exception):
    """Ключ для сервиса не найден ни в env, ни в credentials.json."""

    def __init__(self, service: str):
        self.service = service
        msg = (
            f"\nКлюч для сервиса '{service}' не найден.\n\n"
            f"Чтобы добавить:\n"
            f"  python -m scripts.manage_credentials set {service}\n\n"
            f"Или установи env-переменную {SERVICE_REGISTRY.get(service, {}).get('env', service.upper() + '_API_KEY')}.\n"
        )
        super().__init__(msg)


# Реестр поддерживаемых сервисов: какие env-переменные и инструкции по получению.
SERVICE_REGISTRY = {
    "openai": {
        "env": "OPENAI_API_KEY",
        "description": "OpenAI API key для DALL-E / GPT-image-1 / ChatGPT",
        "how_to_get": "https://platform.openai.com/api-keys (нужен аккаунт + платёжный метод)",
        "format_hint": "sk-...",
    },
    "vk_ads": {
        "env": "VK_ADS_ACCESS_TOKEN",
        "description": "VK Ads API access token для управления кампаниями",
        "how_to_get": "ads.vk.ru → Настройки → API → создать приложение → получить токен",
        "format_hint": "длинная строка ~100+ знаков",
    },
    "runway": {
        "env": "RUNWAY_API_KEY",
        "description": "Runway ML API key для видео-генерации (M2)",
        "how_to_get": "https://app.runwayml.com/account (Settings → API)",
        "format_hint": "key_...",
    },
    "kling": {
        "env": "KLING_API_KEY",
        "description": "Kling AI API key для видео-генерации (M2)",
        "how_to_get": "https://klingai.com → developer console",
        "format_hint": "AccessKey + SecretKey (через ':' в одной строке)",
    },
    "aismm": {
        "env": "AISMM_API_KEY",
        "description": "AiSMM Pro / Контент Машина API (Veo 3.1 + Kling 2.6)",
        "how_to_get": "aismm.pro → Личный кабинет → API",
        "format_hint": "зависит от формата API клиента",
    },
    "anthropic": {
        "env": "ANTHROPIC_API_KEY",
        "description": "Anthropic API key для Claude (некоторые модули)",
        "how_to_get": "https://console.anthropic.com/settings/keys",
        "format_hint": "sk-ant-...",
    },
    "google_genai": {
        "env": "GOOGLE_GENAI_API_KEY",
        "description": "Google AI Studio API key (Veo, Gemini, Imagen)",
        "how_to_get": "https://aistudio.google.com/apikey",
        "format_hint": "AIza...",
    },
    "replicate": {
        "env": "REPLICATE_API_TOKEN",
        "description": "Replicate (универсальный hub видео/image моделей — Kling, Hunyuan, Seedance, Wan, Hailuo, и т.д.)",
        "how_to_get": "https://replicate.com/account/api-tokens",
        "format_hint": "r8_...",
    },
}


def credentials_dir() -> Path:
    """Возвращает путь к директории credentials (создаёт если нет)."""
    base = Path.home() / ".vk-ads-manager"
    base.mkdir(parents=True, exist_ok=True)
    return base


def credentials_file() -> Path:
    return credentials_dir() / "credentials.json"


def _read_all() -> dict:
    """Читает весь credentials.json. Возвращает {} если файла нет."""
    fp = credentials_file()
    if not fp.exists():
        return {}
    try:
        with fp.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"WARN: не смог прочитать credentials.json: {e}", file=sys.stderr)
        return {}


def _write_all(data: dict):
    """Записывает credentials.json с безопасными правами."""
    fp = credentials_file()
    with fp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # На Unix-системах ставим 600 (только владелец читает и пишет)
    if hasattr(os, "chmod") and os.name == "posix":
        try:
            os.chmod(fp, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass


def load_api_key(service: str) -> str:
    """
    Возвращает ключ для сервиса. Сначала env, потом credentials.json.

    Raises:
        CredentialNotFound: если ключа нет ни в env, ни в credentials.json.
    """
    if service not in SERVICE_REGISTRY:
        raise ValueError(f"Неизвестный сервис '{service}'. Зарегистрированные: {list(SERVICE_REGISTRY.keys())}")

    # 1. Env-переменная
    env_name = SERVICE_REGISTRY[service]["env"]
    val = os.environ.get(env_name)
    if val:
        return val

    # 2. credentials.json
    creds = _read_all()
    if service in creds and creds[service]:
        return creds[service]

    # 3. Не нашли
    raise CredentialNotFound(service)


def set_api_key(service: str, key: str):
    """Сохраняет ключ для сервиса в credentials.json."""
    if service not in SERVICE_REGISTRY:
        raise ValueError(f"Неизвестный сервис '{service}'. Зарегистрированные: {list(SERVICE_REGISTRY.keys())}")

    creds = _read_all()
    creds[service] = key
    _write_all(creds)


def delete_api_key(service: str) -> bool:
    """Удаляет ключ из credentials.json. Возвращает True если был."""
    creds = _read_all()
    if service in creds:
        del creds[service]
        _write_all(creds)
        return True
    return False


def list_stored_services() -> list:
    """Возвращает список сервисов с сохранёнными ключами (без самих значений)."""
    return sorted(_read_all().keys())


def get_service_info(service: str) -> dict:
    """Возвращает метаинформацию о сервисе."""
    return SERVICE_REGISTRY.get(service, {})
