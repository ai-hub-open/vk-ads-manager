"""
kling.py — провайдер Kling AI (v1.5 / v1.6 / v2.0 / v2.5).

Документация: https://docs.qingque.cn/d/home/eZQDrAm_AwUoOOQRPb-NSWXm6
Endpoint: https://api.klingai.com/v1/videos/

Аутентификация:
- AccessKey + SecretKey → подписываем JWT (HS256) → передаём как Bearer
- Payload JWT: {iss: <AccessKey>, exp: <now+1800>, nbf: <now-5>}

В credentials хранится строка "AccessKey:SecretKey" (одной записью).
"""

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Optional

from .base import BaseVideoProvider, VideoGenerationError


def _make_jwt(access_key: str, secret_key: str) -> str:
    """Генерирует JWT-токен для Kling API."""
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"iss": access_key, "exp": now + 1800, "nbf": now - 5}

    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(
            json.dumps(d, separators=(",", ":")).encode()
        ).decode().rstrip("=")

    signing_input = f"{b64(header)}.{b64(payload)}"
    sig = hmac.new(secret_key.encode(), signing_input.encode(), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{signing_input}.{sig_b64}"


class KlingProvider(BaseVideoProvider):
    SERVICE_NAME = "kling"
    SUPPORTED_DURATIONS = [5, 10]
    SUPPORTED_ASPECT_RATIOS = ["16:9", "9:16", "1:1"]
    PRICE_PER_SECOND_USD = 0.10  # v1.6 standard

    API_BASE = "https://api.klingai.com/v1/videos"
    POLL_INTERVAL_SEC = 5
    MAX_POLL_ATTEMPTS = 120  # до 10 минут (Kling бывает медленный)

    def _parse_keys(self) -> tuple:
        """Извлекает AccessKey и SecretKey из credentials формата 'AK:SK'."""
        if ":" not in self.api_key:
            raise VideoGenerationError(
                "Kling credentials должны быть в формате 'AccessKey:SecretKey' (одной строкой). "
                "Получи на klingai.com → developer console."
            )
        ak, sk = self.api_key.split(":", 1)
        return ak.strip(), sk.strip()

    def _headers(self) -> dict:
        ak, sk = self._parse_keys()
        token = _make_jwt(ak, sk)
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def generate(
        self,
        prompt: str,
        duration_sec: int = 5,
        aspect_ratio: str = "9:16",
        output_path: Path = None,
        seed_image_path: Optional[Path] = None,
        model: str = "kling-v1-6",
        **kwargs,
    ) -> Path:
        self.validate_params(duration_sec, aspect_ratio)

        try:
            import requests
        except ImportError:
            raise VideoGenerationError("pip install requests")

        # Endpoint и payload зависят от наличия seed image
        if seed_image_path and Path(seed_image_path).exists():
            endpoint = f"{self.API_BASE}/image2video"
            with open(seed_image_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
            payload = {
                "model_name": model,
                "image": img_b64,
                "prompt": prompt,
                "duration": str(duration_sec),
                "aspect_ratio": aspect_ratio,
                "mode": "std",  # std (standard) или pro
            }
        else:
            endpoint = f"{self.API_BASE}/text2video"
            payload = {
                "model_name": model,
                "prompt": prompt,
                "duration": str(duration_sec),
                "aspect_ratio": aspect_ratio,
                "mode": "std",
            }

        # 1. Создаём задачу
        try:
            r = requests.post(endpoint, json=payload, headers=self._headers(), timeout=30)
            if not r.ok:
                raise VideoGenerationError(f"Kling POST {r.status_code}: {r.text[:500]}")
            data = r.json()
            if data.get("code") != 0:
                raise VideoGenerationError(f"Kling API error: {data.get('message')}")
            task_id = data.get("data", {}).get("task_id")
            if not task_id:
                raise VideoGenerationError(f"Kling не вернул task_id: {data}")
        except requests.RequestException as e:
            raise VideoGenerationError(f"Kling request error: {e}")

        print(f"  Kling task created: {task_id}")

        # 2. Polling
        video_url = None
        endpoint_get = endpoint  # тот же URL что и POST, только GET с task_id
        for attempt in range(self.MAX_POLL_ATTEMPTS):
            time.sleep(self.POLL_INTERVAL_SEC)
            try:
                r = requests.get(
                    f"{endpoint_get}/{task_id}", headers=self._headers(), timeout=30
                )
                r.raise_for_status()
                d = r.json()
                if d.get("code") != 0:
                    raise VideoGenerationError(f"Kling poll error: {d.get('message')}")
                td = d.get("data", {})
                status = td.get("task_status")
                if status == "succeed":
                    videos = td.get("task_result", {}).get("videos", [])
                    if videos:
                        video_url = videos[0].get("url")
                    break
                if status in ("failed", "error"):
                    msg = td.get("task_status_msg", "unknown failure")
                    raise VideoGenerationError(f"Kling task failed: {msg}")
                print(f"  …status={status} (attempt {attempt + 1}/{self.MAX_POLL_ATTEMPTS})")
            except requests.RequestException as e:
                print(f"  poll error: {e}, retrying")

        if not video_url:
            raise VideoGenerationError(
                f"Kling не вернул видео за {self.MAX_POLL_ATTEMPTS * self.POLL_INTERVAL_SEC}s"
            )

        # 3. Скачивание
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            r = requests.get(video_url, timeout=120)
            r.raise_for_status()
            output_path.write_bytes(r.content)
        except requests.RequestException as e:
            raise VideoGenerationError(f"Download failed: {e}")

        return output_path
