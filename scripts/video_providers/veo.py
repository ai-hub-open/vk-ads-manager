"""
veo.py — провайдер Google Veo (через Gemini / AI Studio API).

Документация: https://ai.google.dev/gemini-api/docs/video-generation

Endpoint: https://generativelanguage.googleapis.com/v1beta/models/{model}:predictLongRunning
Polling:  https://generativelanguage.googleapis.com/v1beta/{operation_name}

На 2026-05:
- veo-3.0-generate-001 — стабильная Veo 3
- veo-3.1-generate-preview — превью с лучшим качеством
- veo-fast-generate — быстрее и дешевле

Аутентификация: API key через query-параметр `?key=<API_KEY>`.

Ключ хранится в credentials под именем `google_genai`. Если его нет в registry —
добавь в SERVICE_REGISTRY (или используем общую запись `anthropic` неправильно —
лучше отдельную).
"""

import base64
import time
from pathlib import Path
from typing import Optional

from .base import BaseVideoProvider, VideoGenerationError


class VeoProvider(BaseVideoProvider):
    SERVICE_NAME = "google_genai"  # требует записи в SERVICE_REGISTRY
    SUPPORTED_DURATIONS = [4, 5, 6, 7, 8]
    SUPPORTED_ASPECT_RATIOS = ["16:9", "9:16"]  # 1:1 не поддерживается нативно
    PRICE_PER_SECOND_USD = 0.50

    API_BASE = "https://generativelanguage.googleapis.com/v1beta"
    POLL_INTERVAL_SEC = 10
    MAX_POLL_ATTEMPTS = 60  # до 10 минут

    def generate(
        self,
        prompt: str,
        duration_sec: int = 5,
        aspect_ratio: str = "9:16",
        output_path: Path = None,
        seed_image_path: Optional[Path] = None,
        model: str = "veo-3.0-generate-001",
        **kwargs,
    ) -> Path:
        self.validate_params(duration_sec, aspect_ratio)

        try:
            import requests
        except ImportError:
            raise VideoGenerationError("pip install requests")

        # Build request
        instances = [{"prompt": prompt}]

        # Image-to-video
        if seed_image_path and Path(seed_image_path).exists():
            mime = "image/png" if str(seed_image_path).endswith(".png") else "image/jpeg"
            with open(seed_image_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
            instances[0]["image"] = {"bytesBase64Encoded": img_b64, "mimeType": mime}

        payload = {
            "instances": instances,
            "parameters": {
                "aspectRatio": aspect_ratio,
                "durationSeconds": duration_sec,
                "numberOfVideos": 1,
            },
        }

        # POST → long-running operation
        url = f"{self.API_BASE}/models/{model}:predictLongRunning?key={self.api_key}"
        try:
            r = requests.post(url, json=payload, timeout=60)
            if not r.ok:
                raise VideoGenerationError(f"Veo POST {r.status_code}: {r.text[:500]}")
            op = r.json()
            op_name = op.get("name")
            if not op_name:
                raise VideoGenerationError(f"Veo не вернул operation name: {op}")
        except requests.RequestException as e:
            raise VideoGenerationError(f"Veo request error: {e}")

        print(f"  Veo operation: {op_name}")

        # Polling
        video_uri = None
        for attempt in range(self.MAX_POLL_ATTEMPTS):
            time.sleep(self.POLL_INTERVAL_SEC)
            try:
                r = requests.get(
                    f"{self.API_BASE}/{op_name}?key={self.api_key}", timeout=30
                )
                r.raise_for_status()
                d = r.json()
                if d.get("done"):
                    err = d.get("error")
                    if err:
                        raise VideoGenerationError(f"Veo operation failed: {err}")
                    resp = d.get("response", {})
                    videos = (
                        resp.get("generateVideoResponse", {})
                        .get("generatedSamples", [])
                    )
                    if videos:
                        video = videos[0].get("video", {})
                        # Веоможет вернуть URI или base64
                        video_uri = video.get("uri") or video.get("bytesBase64Encoded")
                    break
                print(f"  …processing (attempt {attempt + 1}/{self.MAX_POLL_ATTEMPTS})")
            except requests.RequestException as e:
                print(f"  poll error: {e}")

        if not video_uri:
            raise VideoGenerationError(
                f"Veo не вернул видео за {self.MAX_POLL_ATTEMPTS * self.POLL_INTERVAL_SEC}s"
            )

        # Скачивание (если URI) или декодинг base64
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if video_uri.startswith("http"):
            try:
                r = requests.get(f"{video_uri}?key={self.api_key}", timeout=120)
                r.raise_for_status()
                output_path.write_bytes(r.content)
            except requests.RequestException as e:
                raise VideoGenerationError(f"Veo download failed: {e}")
        else:
            # base64
            output_path.write_bytes(base64.b64decode(video_uri))

        return output_path
