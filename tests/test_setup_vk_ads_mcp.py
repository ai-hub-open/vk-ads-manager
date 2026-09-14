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


# ---------- персональная ссылка подключения (--connection-url) ----------

CONNECTION_URL = "https://vkads-mcp.aihub.click.ru/o/CONN123/tok-abcdef123456789"


def test_connection_url_writes_entry_without_headers(tmp_path):
    """Креды зашиты в адрес — пустой блок headers в конфиг писать не надо."""
    rc = setup_vk_ads_mcp.main(
        ["--connection-url", CONNECTION_URL, "--target", "claude-code"]
    )
    assert rc == 0
    entry = _servers(tmp_path)["vk-ads"]
    assert entry["url"] == CONNECTION_URL
    assert entry["type"] == "http"
    assert "headers" not in entry


def test_connection_url_accepts_mcp_suffix(tmp_path):
    """Сервер принимает и с /mcp на конце, и без него."""
    rc = setup_vk_ads_mcp.main(
        ["--connection-url", CONNECTION_URL + "/mcp", "--target", "claude-code"]
    )
    assert rc == 0
    assert _servers(tmp_path)["vk-ads"]["url"] == CONNECTION_URL + "/mcp"


def test_connection_url_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("VK_ADS_CONNECTION_URL", CONNECTION_URL)
    rc = setup_vk_ads_mcp.main(["--target", "claude-code"])
    assert rc == 0
    assert _servers(tmp_path)["vk-ads"]["url"] == CONNECTION_URL


@pytest.mark.parametrize("bad", [
    "https://example.com/mcp",                      # не та форма
    "https://vkads-mcp.aihub.click.ru/mcp",         # обычный endpoint без кредов
    "http://vkads-mcp.aihub.click.ru/o/C/tok",      # не https
    "https://vkads-mcp.aihub.click.ru/o/CONN123",   # нет токена
    "not-a-url",
])
def test_connection_url_rejects_malformed(bad, tmp_path, capsys):
    rc = setup_vk_ads_mcp.main(["--connection-url", bad, "--target", "claude-code"])
    assert rc == 1
    assert "не похожа на персональную" in capsys.readouterr().err
    assert not (tmp_path / ".mcp.json").exists()


def test_connection_url_skips_keepimage(tmp_path):
    """Токена click.ru в ссылке нет — подключать KeepImage нечем."""
    rc = setup_vk_ads_mcp.main(
        ["--connection-url", CONNECTION_URL, "--target", "claude-code"]
    )
    assert rc == 0
    assert "keepimage" not in _servers(tmp_path)


def test_connection_url_skips_registry():
    rc = setup_vk_ads_mcp.main(
        ["--connection-url", CONNECTION_URL, "--target", "claude-code"]
    )
    assert rc == 0
    with pytest.raises(CredentialNotFound):
        load_api_key("clickru")


def test_connection_url_claude_desktop_has_no_header_args(tmp_path, monkeypatch):
    """Мост mcp-remote получает только адрес: заголовков в этом режиме нет."""
    monkeypatch.setattr(setup_vk_ads_mcp, "claude_desktop_config",
                        lambda: tmp_path / "claude_desktop_config.json")
    monkeypatch.setitem(setup_vk_ads_mcp.TARGETS, "claude-desktop",
                        lambda: tmp_path / "claude_desktop_config.json")
    rc = setup_vk_ads_mcp.main(
        ["--connection-url", CONNECTION_URL, "--target", "claude-desktop"]
    )
    assert rc == 0
    data = json.loads((tmp_path / "claude_desktop_config.json").read_text(encoding="utf-8"))
    args = data["mcpServers"]["vk-ads"]["args"]
    assert args == ["-y", "mcp-remote", CONNECTION_URL]
    assert "--header" not in args


def test_connection_url_token_masked_in_output(capsys, tmp_path):
    """Секрет не должен светиться в логах — в конфиг он пишется целиком."""
    setup_vk_ads_mcp.main(["--connection-url", CONNECTION_URL, "--target", "claude-code"])
    out = capsys.readouterr().out
    assert "tok-abcdef123456789" not in out
    assert "tok-...89" in out
    # но в файле — полная ссылка, иначе подключение не заработает
    assert _servers(tmp_path)["vk-ads"]["url"] == CONNECTION_URL


def test_connection_url_wins_over_clickru_token(tmp_path):
    """Явная ссылка отменяет путь click.ru: заголовки не пишутся, реестр не трогается."""
    rc = setup_vk_ads_mcp.main(
        ["--connection-url", CONNECTION_URL, "--token", TOKEN,
         "--vk-account-id", ACCOUNT, "--target", "claude-code"]
    )
    assert rc == 0
    servers = _servers(tmp_path)
    assert "headers" not in servers["vk-ads"]
    assert "keepimage" not in servers
    with pytest.raises(CredentialNotFound):
        load_api_key("clickru")


def test_no_creds_hint_mentions_connection_url(capsys):
    rc = setup_vk_ads_mcp.main(["--target", "claude-code"])
    assert rc == 1
    assert "--connection-url" in capsys.readouterr().err


def test_remove_still_works_after_connection_url(tmp_path):
    setup_vk_ads_mcp.main(["--connection-url", CONNECTION_URL, "--target", "claude-code"])
    assert "vk-ads" in _servers(tmp_path)
    rc = setup_vk_ads_mcp.main(["--remove", "--target", "claude-code"])
    assert rc == 0
    assert "vk-ads" not in _servers(tmp_path)


def test_clickru_path_still_writes_headers(tmp_path):
    """Регрессия: обычный путь не должен пострадать от нового флага."""
    rc = setup_vk_ads_mcp.main(
        ["--token", TOKEN, "--vk-account-id", ACCOUNT, "--target", "claude-code"]
    )
    assert rc == 0
    headers = _servers(tmp_path)["vk-ads"]["headers"]
    assert headers["X-Click-Ru-Token"] == TOKEN
    assert headers["X-Click-Ru-Account-Id"] == ACCOUNT
