"""Тесты установщика хостового MCP VK (`scripts/setup_vk_ads_mcp.py`).

Проверяют, что при подключении по пути click.ru токен уезжает в реестр ключей
(сервис `clickru`), а на путях `--dry-run`/`--remove`/`--vk-ads-token` — нет.
Сеть замокана (conftest запрещает connect), HOME изолирован в tmp, конфиги
клиента пишутся в tmp-cwd (`--target claude-code` → `.mcp.json` в cwd).
"""
from __future__ import annotations

import json

import pytest

from scripts import setup_vk_ads_mcp
from scripts.credentials import CredentialNotFound, load_api_key

TOKEN = "clickru-token-abcdef123456"
ACCOUNT = "999"
USER_ID = "777"


@pytest.fixture(autouse=True)
def _cwd_tmp(tmp_path, monkeypatch):
    """`claude-code` пишет `.mcp.json` в cwd — уводим cwd в tmp, чтобы не трогать репо."""
    monkeypatch.chdir(tmp_path)


def _servers(tmp_path) -> dict:
    """mcpServers из записанного `.mcp.json` (target claude-code); {} если ключ снят."""
    data = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    return data.get("mcpServers", {})


def test_saves_clickru_token_to_registry():
    rc = setup_vk_ads_mcp.main(
        ["--token", TOKEN, "--vk-account-id", ACCOUNT, "--target", "claude-code"]
    )
    assert rc == 0
    assert load_api_key("clickru") == TOKEN


def test_saves_user_id_when_given():
    rc = setup_vk_ads_mcp.main(
        ["--token", TOKEN, "--vk-account-id", ACCOUNT,
         "--click-ru-user-id", USER_ID, "--target", "claude-code"]
    )
    assert rc == 0
    assert load_api_key("clickru") == TOKEN
    assert load_api_key("clickru_user_id") == USER_ID


def test_dry_run_does_not_write_registry(capsys):
    rc = setup_vk_ads_mcp.main(
        ["--token", TOKEN, "--vk-account-id", ACCOUNT,
         "--target", "claude-code", "--dry-run"]
    )
    assert rc == 0
    assert "dry-run" in capsys.readouterr().out.lower()
    with pytest.raises(CredentialNotFound):
        load_api_key("clickru")


def test_remove_does_not_touch_registry():
    rc = setup_vk_ads_mcp.main(["--remove", "--target", "claude-code"])
    assert rc == 0
    with pytest.raises(CredentialNotFound):
        load_api_key("clickru")


def test_vk_ads_token_path_skips_registry():
    rc = setup_vk_ads_mcp.main(
        ["--vk-ads-token", "eyJ0-ready-vk-token", "--target", "claude-code"]
    )
    assert rc == 0
    with pytest.raises(CredentialNotFound):
        load_api_key("clickru")


def test_keepimage_connector_added_on_clickru_path(tmp_path):
    rc = setup_vk_ads_mcp.main(
        ["--token", TOKEN, "--vk-account-id", ACCOUNT, "--target", "claude-code"]
    )
    assert rc == 0
    servers = _servers(tmp_path)
    assert "vk-ads" in servers
    assert "keepimage" in servers
    kp = servers["keepimage"]
    assert kp["url"] == setup_vk_ads_mcp.KEEPIMAGE_URL
    assert kp["headers"]["X-Auth-Token"] == TOKEN
    assert "X-Auth-UserId" not in kp["headers"]


def test_keepimage_user_id_header(tmp_path):
    rc = setup_vk_ads_mcp.main(
        ["--token", TOKEN, "--vk-account-id", ACCOUNT,
         "--click-ru-user-id", USER_ID, "--target", "claude-code"]
    )
    assert rc == 0
    assert _servers(tmp_path)["keepimage"]["headers"]["X-Auth-UserId"] == USER_ID


def test_no_keepimage_flag_skips_connector(tmp_path):
    rc = setup_vk_ads_mcp.main(
        ["--token", TOKEN, "--vk-account-id", ACCOUNT,
         "--target", "claude-code", "--no-keepimage"]
    )
    assert rc == 0
    servers = _servers(tmp_path)
    assert "vk-ads" in servers
    assert "keepimage" not in servers


def test_vk_ads_token_path_skips_keepimage(tmp_path):
    rc = setup_vk_ads_mcp.main(
        ["--vk-ads-token", "eyJ0-ready-vk-token", "--target", "claude-code"]
    )
    assert rc == 0
    assert "keepimage" not in _servers(tmp_path)


def test_remove_drops_keepimage(tmp_path):
    setup_vk_ads_mcp.main(
        ["--token", TOKEN, "--vk-account-id", ACCOUNT, "--target", "claude-code"]
    )
    assert "keepimage" in _servers(tmp_path)
    rc = setup_vk_ads_mcp.main(["--remove", "--target", "claude-code"])
    assert rc == 0
    servers = _servers(tmp_path)
    assert "vk-ads" not in servers
    assert "keepimage" not in servers


def test_registry_failure_does_not_break_install(monkeypatch, capsys):
    def boom(*_a, **_kw):
        raise RuntimeError("диск переполнен")

    monkeypatch.setattr(setup_vk_ads_mcp, "set_api_key", boom)
    rc = setup_vk_ads_mcp.main(
        ["--token", TOKEN, "--vk-account-id", ACCOUNT, "--target", "claude-code"]
    )
    assert rc == 0  # установка MCP не должна падать из-за реестра
    assert "не сохранён в реестр" in capsys.readouterr().out
    with pytest.raises(CredentialNotFound):
        load_api_key("clickru")
