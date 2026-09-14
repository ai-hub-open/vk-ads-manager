"""Общие фикстуры: изоляция HOME/env и запрет сети.

Тесты скилла не ходят в VK Ads, OpenAI, KeepImage и провайдеров видео: любой
реальный connect падает AssertionError. Реальные ключи разработчика в
~/.vk-ads-manager не трогаем — HOME подменяется на tmp_path.
"""
from __future__ import annotations

import socket

import pytest

_SERVICE_ENV = (
    "OPENAI_API_KEY",
    "VK_ADS_ACCESS_TOKEN",
    "CLICK_RU_TOKEN",
    "CLICK_RU_USER_ID",
    "CLICK_RU_ACCOUNT_ID",
    "RUNWAY_API_KEY",
    "KLING_API_KEY",
    "AISMM_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_GENAI_API_KEY",
    "REPLICATE_API_TOKEN",
)


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


@pytest.fixture(autouse=True)
def _clear_service_env(monkeypatch):
    for name in _SERVICE_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def deny(*_a, **_kw):
        raise AssertionError("тест полез в сеть — нужен мок")

    monkeypatch.setattr(socket.socket, "connect", deny)
