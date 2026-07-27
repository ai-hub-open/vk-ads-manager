"""
runway.py — провайдер Runway ML (Gen-4 / Gen-3 Alpha).

Документация API: https://docs.dev.runwayml.com/

API workflow:
1. POST /v1/image_to_video или text_to_video → task_id
2. GET /v1/tasks/{task_id} — polling до status=SUCCEEDED
3. Скачать готовое видео по URL из ответа

Лимиты:
- Длительность: 5 или 10 секунд
- Aspect ratios: 16:9, 9:16, 4:3, 3:4, 1:1
- Цена Gen-4 turbo: ~$0.05/sec → 5s = $0.25, 10s = $0.50
"""

import time
from pathlib import Path
from typing import Optional

from .base import BaseVideoProvider, VideoGenerationError


class RunwayProvider(BaseVideoProvider):
    SERVICE_NAME = "runway"
    SUPPORTED_DURATIONS = [5, 10]
    SUPPORTED_ASPECT_RATIOS = ["16:9", "9:16", "4:3", "3:4", "1:1"]
    PRICE_PER_SECOND_USD = 0.05  # Gen-4 turbo

    API_BASE = "https://api.dev.runwayml.com/v1"
    POLL_INTERVAL_SEC = 5
    MAX_POLL_ATTEMPTS = 60  # ~5 минут максимум

    def generate(
        self,
        prompt: str,
        duration_sec: int = 5,
        aspect_ratio: str = "9:16",
        output_path: Path = None,
        seed_image_path: Optional[Path] = None,
        model: str = "gen4_turbo",
        **kwargs,
    ) -> Path:
        self.validate_params(duration_sec, aspect_ratio)

        try:
            import requests
        except ImportError:
            raise VideoGenerationError("Нужен `pip install requests`")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Runway-Version": "2024-11-06",
        }

        # Если есть seed image — image_to_video; иначе text_to_video
        if seed_image_path and Path(seed_image_path).exists():
            endpoint = f"{self.API_BASE}/image_to_video"
            # Загружаем seed image как base64 data URI
            import base64
            mime = "image/png" if str(seed_image_path).endswith(".png") else "image/jpeg"
            with open(seed_image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            payload = {
                "model": model,
                "promptImage": f"data:{mime};base64,{b64}",
                "promptText": prompt,
                "duration": duration_sec,
                "ratio": aspect_ratio.replace(":", "x"),  # Runway: 9x16 а не 9:16
            }
        else:
            endpoint = f"{self.API_BASE}/text_to_video"
            payload = {
                "model": model,
                "promptText": prompt,
                "duration": duration_sec,
                "ratio": aspect_ratio.replace(":", "x"),
            }

        # POST — создаём задачу
        try:
            r = requests.post(endpoint, json=payload, headers=headers, timeout=30)
            if not r.ok:
                raise VideoGenerationError(
                    f"Runway POST failed {r.status_code}: {r.text[:500]}"
                )
            task_id = r.json().get("id")
            if not task_id:
                raise VideoGenerationError(f"Runway не вернул task_id: {r.json()}")
        except requests.RequestException as e:
            raise VideoGenerationError(f"Runway request error: {e}")

        # Polling — ждём готовности
        print(f"  Runway task created: {task_id}, polling...")
        video_url = None
        for attempt in range(self.MAX_POLL_ATTEMPTS):
            time.sleep(self.POLL_INTERVAL_SEC)
            try:
                r = requests.get(
                    f"{self.API_BASE}/tasks/{task_id}", headers=headers, timeout=30
                )
                r.raise_for_status()
                data = r.json()
                status = data.get("status")
                if status == "SUCCEEDED":
                    output = data.get("output", [])
                    if output:
                        video_url = output[0] if isinstance(output, list) else output
                    break
                elif status == "FAILED":
                    raise VideoGenerationError(
                        f"Runway task failed: {data.get('failure', 'unknown')}"
                    )
                # PENDING / RUNNING — продолжаем ждать
                print(f"  …status={status} (attempt {attempt + 1}/{self.MAX_POLL_ATTEMPTS})")
            except requests.RequestException as e:
                print(f"  poll error: {e}, retrying")

        if not video_url:
            raise VideoGenerationError(
                f"Runway не вернул видео за {self.MAX_POLL_ATTEMPTS * self.POLL_INTERVAL_SEC}s"
            )

        # Скачиваем
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            r = requests.get(video_url, timeout=120)
            r.raise_for_status()
            output_path.write_bytes(r.content)
        except requests.RequestException as e:
            raise VideoGenerationError(f"Download failed: {e}")

        return output_path
