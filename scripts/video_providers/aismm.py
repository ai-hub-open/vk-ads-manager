"""
aismm.py — провайдер AiSMM Pro / Контент Машина.

AiSMM Pro использует Veo 3.1 + Kling 2.6 под капотом. Доступ через API
владельцам подписки.

API base URL и формат payload зависят от их реализации — если у тебя нет
точных endpoint'ов, начни с этой generic-реализации и подправь под фактические
параметры.

Что нужно узнать у aismm.pro → личный кабинет → API:
1. base URL (например https://api.aismm.pro/v1/)
2. endpoint для text-to-video / image-to-video
3. формат payload (поля: prompt, duration, aspect_ratio, etc)
4. формат polling и поле task_id
5. формат ответа со ссылкой на готовое видео

Generic-реализация ниже работает по REST-pattern: POST → task_id → GET polling
до status=ready → download. Это типичный паттерн для большинства SaaS.

Если у AiSMM Pro другой паттерн (sync с длинным ожиданием, websockets,
file-streaming) — нужна более глубокая адаптация.
"""

import os
import time
from pathlib import Path
from typing import Optional

from .base import BaseVideoProvider, VideoGenerationError


class AISMMProvider(BaseVideoProvider):
    SERVICE_NAME = "aismm"
    SUPPORTED_DURATIONS = [5, 10, 15, 30]
    SUPPORTED_ASPECT_RATIOS = ["9:16", "1:1", "16:9"]
    PRICE_PER_SECOND_USD = None  # внутренние токены

    # Может быть переопределён через env AISMM_API_BASE — для тестирования
    DEFAULT_API_BASE = "https://api.aismm.pro/v1"
    POLL_INTERVAL_SEC = 5
    MAX_POLL_ATTEMPTS = 120

    def __init__(self, api_key: Optional[str] = None, api_base: Optional[str] = None):
        super().__init__(api_key=api_key)
        self.api_base = api_base or os.environ.get("AISMM_API_BASE", self.DEFAULT_API_BASE)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def generate(
        self,
        prompt: str,
        duration_sec: int = 5,
        aspect_ratio: str = "9:16",
        output_path: Path = None,
        seed_image_path: Optional[Path] = None,
        model: str = "veo-3.1",  # либо "kling-2.6"
        **kwargs,
    ) -> Path:
        """
        Generic-реализация. Если AiSMM Pro имеет другой API — переопредели
        endpoints и payload structure здесь.
        """
        self.validate_params(duration_sec, aspect_ratio)

        try:
            import requests
        except ImportError:
            raise VideoGenerationError("pip install requests")

        # Endpoint
        if seed_image_path and Path(seed_image_path).exists():
            endpoint = f"{self.api_base}/videos/image-to-video"
            import base64
            with open(seed_image_path, "rb") as f:
                seed_b64 = base64.b64encode(f.read()).decode()
            payload = {
                "model": model,
                "prompt": prompt,
                "seed_image_base64": seed_b64,
                "duration_seconds": duration_sec,
                "aspect_ratio": aspect_ratio,
            }
        else:
            endpoint = f"{self.api_base}/videos/text-to-video"
            payload = {
                "model": model,
                "prompt": prompt,
                "duration_seconds": duration_sec,
                "aspect_ratio": aspect_ratio,
            }

        # POST
        try:
            r = requests.post(endpoint, json=payload, headers=self._headers(), timeout=60)
            if not r.ok:
                raise VideoGenerationError(f"AiSMM POST {r.status_code}: {r.text[:500]}")
            data = r.json()
            task_id = data.get("task_id") or data.get("id")
            if not task_id:
                raise VideoGenerationError(f"AiSMM не вернул task_id: {data}")
        except requests.RequestException as e:
            raise VideoGenerationError(f"AiSMM request error: {e}")

        print(f"  AiSMM task created: {task_id}")

        # Polling
        video_url = None
        for attempt in range(self.MAX_POLL_ATTEMPTS):
            time.sleep(self.POLL_INTERVAL_SEC)
            try:
                r = requests.get(
                    f"{self.api_base}/videos/{task_id}", headers=self._headers(), timeout=30
                )
                r.raise_for_status()
                d = r.json()
                status = d.get("status", "processing")
                if status in ("ready", "succeeded", "completed", "done"):
                    video_url = d.get("video_url") or d.get("output_url") or d.get("url")
                    break
                if status in ("failed", "error"):
                    raise VideoGenerationError(f"AiSMM task failed: {d.get('error') or d}")
                print(f"  …status={status} (attempt {attempt + 1}/{self.MAX_POLL_ATTEMPTS})")
            except requests.RequestException as e:
                print(f"  poll error: {e}")

        if not video_url:
            raise VideoGenerationError(
                f"AiSMM не вернул видео за {self.MAX_POLL_ATTEMPTS * self.POLL_INTERVAL_SEC}s"
            )

        # Download
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            r = requests.get(video_url, timeout=120)
            r.raise_for_status()
            output_path.write_bytes(r.content)
        except requests.RequestException as e:
            raise VideoGenerationError(f"AiSMM download failed: {e}")

        return output_path
