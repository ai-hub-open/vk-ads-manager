"""
video_providers — провайдер-абстракция для генерации видео.

Каждый провайдер реализует BaseVideoProvider и регистрируется в PROVIDER_REGISTRY.

Использование:
    from scripts.video_providers import get_provider

    provider = get_provider("runway")  # auto-loads API key
    video_path = provider.generate(
        prompt="...",
        duration_sec=5,
        aspect_ratio="9:16",
        output_path=Path("video.mp4"),
    )
"""

from .base import BaseVideoProvider, VideoGenerationError
from .runway import RunwayProvider
from .kling import KlingProvider
from .aismm import AISMMProvider
from .veo import VeoProvider
from .replicate import ReplicateProvider


PROVIDER_REGISTRY = {
    "runway": RunwayProvider,
    "kling": KlingProvider,
    "aismm": AISMMProvider,
    "veo": VeoProvider,
    "replicate": ReplicateProvider,
}


def get_provider(name: str, api_key: str = None, model_id: str = None) -> BaseVideoProvider:
    """
    Возвращает инстанс провайдера. Если api_key не передан — провайдер
    сам пробует загрузить через scripts.credentials.load_api_key().

    model_id релевантен только для replicate (для остальных модель задаётся
    через kwargs при generate()).
    """
    if name not in PROVIDER_REGISTRY:
        available = ", ".join(PROVIDER_REGISTRY.keys())
        raise ValueError(f"Неизвестный провайдер '{name}'. Доступны: {available}")
    cls = PROVIDER_REGISTRY[name]
    if name == "replicate":
        return cls(api_key=api_key, model_id=model_id)
    return cls(api_key=api_key)


__all__ = [
    "BaseVideoProvider",
    "VideoGenerationError",
    "RunwayProvider",
    "KlingProvider",
    "AISMMProvider",
    "VeoProvider",
    "ReplicateProvider",
    "PROVIDER_REGISTRY",
    "get_provider",
]
