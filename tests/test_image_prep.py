"""Квадрат 1080 → 600×600 перед загрузкой (роль image_600x600 принимает ровно 600).

Проверяется общий хелпер и оба места, где картинки уходят наружу: KeepImage
(путь A, `upload_creatives_to_storage.py`) и REST-загрузка (путь B, `MediaAPI`).
Исходник на диске меняться не должен.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from scripts import _image_prep as prep
import scripts.upload_creatives_to_storage as up
from scripts.vk_ads_api import VKAdsClient


def _png(w: int, h: int, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, format=fmt)
    return buf.getvalue()


def _size(blob: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(blob)) as img:
        return img.size


# ---------- хелпер ----------

@pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
def test_square_1080_shrinks_to_600(fmt):
    out, info = prep.fit_square(_png(1080, 1080, fmt))
    assert _size(out) == (600, 600)
    assert info["resized"] and info["original"] == "1080x1080"
    assert (info["width"], info["height"]) == (600, 600)


@pytest.mark.parametrize("w, h", [(1080, 607), (1080, 1350), (600, 600), (500, 500)])
def test_fit_square_leaves_other_sizes(w, h):
    blob = _png(w, h)
    out, info = prep.fit_square(blob)
    assert out == blob
    assert not info["resized"]
    assert (info["width"], info["height"]) == (w, h)


def test_not_an_image_passes_through():
    blob = b"\x89PNG\r\n\x1a\n" + b"0" * 32  # битый «png» из старых тестов
    out, info = prep.fit_square(blob)
    assert out == blob and info["width"] is None and not info["resized"]


def test_logo_side():
    out, info = prep.fit_square(_png(512, 512), side=prep.LOGO_SIDE)
    assert _size(out) == (256, 256)


@pytest.mark.parametrize("w, h, role", [
    (600, 600, "image_600x600"), (1080, 607, "image_1080x607"),
    (1080, 1350, "image_1080x1350"), (1000, 300, None), (None, None, None),
])
def test_image_role(w, h, role):
    assert prep.image_role(w, h) == role


# ---------- путь A: KeepImage ----------

class _Resp:
    status_code = 200
    text = ""

    def json(self):
        return {"id": "x", "url": "https://storage.aihub.click.ru/f/x/a.png"}


def test_keepimage_upload_sends_600_and_keeps_source(tmp_path, monkeypatch):
    src = tmp_path / "a.png"
    original = _png(1080, 1080)
    src.write_bytes(original)
    sent = {}

    def fake_put(url, data=None, headers=None, timeout=None):
        sent["data"] = data
        return _Resp()

    monkeypatch.setattr(up.requests, "put", fake_put)
    rec = up.upload_one(src, token="T", ttl=60)

    assert rec["status"] == "ok"
    assert rec["resized_from"] == "1080x1080"
    assert _size(sent["data"]) == (600, 600)
    assert src.read_bytes() == original, "исходник не должен меняться"


def test_keepimage_horizontal_untouched(tmp_path, monkeypatch):
    src = tmp_path / "h.png"
    original = _png(1080, 607)
    src.write_bytes(original)
    sent = {}
    monkeypatch.setattr(up.requests, "put",
                        lambda url, data=None, headers=None, timeout=None: sent.update(data=data) or _Resp())
    rec = up.upload_one(src, token="T", ttl=60)
    assert sent["data"] == original
    assert "resized_from" not in rec


# ---------- путь B: REST ----------

def test_rest_upload_sends_600_and_reports_size(tmp_path, monkeypatch):
    src = tmp_path / "sq.png"
    original = _png(1080, 1080)
    src.write_bytes(original)
    live = VKAdsClient(access_token="T", dry_run=False)
    sent = {}

    def fake_upload(path, files, timeout=300):
        sent["files"] = files
        return {"id": 1}

    monkeypatch.setattr(live, "upload", fake_upload)
    resp = live.media.upload_image(src)

    _, blob, mime = sent["files"]["file"]
    assert _size(blob) == (600, 600) and mime == "image/png"
    assert (resp["width"], resp["height"]) == (600, 600)
    assert resp["resized_from"] == "1080x1080"
    assert src.read_bytes() == original


def test_rest_logo_shrinks_to_256(tmp_path, monkeypatch):
    src = tmp_path / "logo.png"
    src.write_bytes(_png(1024, 1024))
    live = VKAdsClient(access_token="T", dry_run=False)
    sent = {}
    monkeypatch.setattr(live, "upload", lambda path, files, timeout=300: sent.update(f=files) or {"id": 2})
    live.media.upload_logo(src)
    assert _size(sent["f"]["file"][1]) == (256, 256)


# ---------- точные размеры ролей (prepare_image) ----------

@pytest.mark.parametrize("w, h, target, role", [
    (1080, 1080, (600, 600), "image_600x600"),
    (1920, 1080, (1080, 607), "image_1080x607"),
    (1080, 1350, (1080, 1350), "image_1080x1350"),
    (2160, 2700, (1080, 1350), "image_1080x1350"),
])
def test_prepare_image_exact_role_size(w, h, target, role):
    out, info = prep.prepare_image(_png(w, h))
    assert _size(out) == target
    assert info["role"] == role


def test_prepare_image_odd_ratio_not_cropped():
    blob = _png(1000, 300)
    out, info = prep.prepare_image(blob)
    assert out == blob and info["role"] is None


def test_prepare_image_too_small_not_upscaled():
    blob = _png(640, 360)
    out, info = prep.prepare_image(blob)
    assert out == blob and info["too_small"] and info["role"] == "image_1080x607"


def test_no_pillow_warns_loudly(monkeypatch, capsys):
    monkeypatch.setattr(prep, "_load_pil", lambda: None)
    monkeypatch.setattr(prep, "_warned_no_pillow", False)
    blob = b"anything"
    out, info = prep.fit_square(blob)
    assert out == blob and info["pillow_missing"]
    err = capsys.readouterr().err
    assert "Pillow не установлен" in err and "install.py" in err


# ---------- путь A: логотип отдельно, 256×256 ----------

def test_keepimage_logo_shrinks_to_256_and_marked(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / "assets" / "images").mkdir(parents=True)
    (ws / "assets" / "brand").mkdir(parents=True)
    (ws / "assets" / "images" / "cr1.png").write_bytes(_png(1080, 1080))
    logo_src = ws / "assets" / "brand" / "logo.png"
    logo_src.write_bytes(_png(1024, 1024))
    sent = {}

    def fake_put(url, data=None, headers=None, timeout=None):
        sent[url.rsplit("/", 1)[-1]] = data
        return _Resp()

    monkeypatch.setattr(up.requests, "put", fake_put)
    monkeypatch.setattr(up, "_token", lambda: "T")
    assert up.main(["--workspace", str(ws)]) == 0

    assert _size(sent["logo.png"]) == (256, 256)
    assert _size(sent["cr1.png"]) == (600, 600)
    manifest = json.loads((ws / "assets" / "storage_manifest.json").read_text(encoding="utf-8"))
    kinds = {Path(r["local"]).name: r["kind"] for r in manifest}
    assert kinds == {"cr1.png": "image", "logo.png": "logo"}
    assert _size(logo_src.read_bytes()) == (1024, 1024), "исходник логотипа не меняется"


def test_keepimage_logo_in_images_dir_not_treated_as_600(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / "assets" / "images").mkdir(parents=True)
    (ws / "assets" / "images" / "logo.png").write_bytes(_png(1080, 1080))
    sent = {}
    monkeypatch.setattr(up.requests, "put",
                        lambda url, data=None, headers=None, timeout=None: sent.update({url: data}) or _Resp())
    monkeypatch.setattr(up, "_token", lambda: "T")
    up.main(["--workspace", str(ws)])
    assert len(sent) == 1
    assert _size(list(sent.values())[0]) == (256, 256)
