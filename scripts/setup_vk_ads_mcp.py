"""
setup_vk_ads_mcp.py — подключает хостовый MCP «VK Реклама» (aihub.click.ru) к MCP-клиентам.

Сервер уже поднят на стороне aihub — клонировать репозиторий и ставить Bun
НЕ нужно. Скрипт только дописывает блок `mcpServers.vk-ads` в конфиги клиентов
(с бэкапом; существующие серверы сохраняются).

Сервер:
- vk-ads → https://vkads-mcp.aihub.click.ru/mcp (45 инструментов VK Ads API, имена vk_ads_*)

Авторизация (один из вариантов):
A. Через click.ru (основной): --token <CLICK_RU_TOKEN> + --vk-account-id <ID>
   Токен: https://click.ru/userinfo.html → «API Token».
   ID аккаунта VK Рекламы в click.ru: GET /accounts в https://api.click.ru/V0/docs/.
B. Готовый access_token VK Ads: --vk-ads-token <eyJ0...>

Цели (--target, можно несколько):
- cursor          ~/.cursor/mcp.json (глобально для Cursor)
- cursor-project  .cursor/mcp.json в текущей папке
- claude-code     .mcp.json в текущей папке
- claude-desktop  claude_desktop_config.json (через stdio-мост mcp-remote, нужен Node.js)
- all             cursor + claude-code + claude-desktop

Использование:
    python -m scripts.setup_vk_ads_mcp \
        --token <CLICK_RU_TOKEN> --vk-account-id <ID> --target all
    python -m scripts.setup_vk_ads_mcp --remove --target cursor

Скрипт ничего не запускает и не отправляет наружу; токен в логах маскируется.
После записи — перезапусти клиента и проверь связь инструментом vk_ads_auth_check.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
from pathlib import Path

try:
    from scripts.credentials import set_api_key
except ImportError:  # запуск напрямую, не как модуль пакета
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from scripts.credentials import set_api_key

VK_ADS_URL = "https://vkads-mcp.aihub.click.ru/mcp"
VK_ADS_SERVER = "vk-ads"

# KeepImage — временное хранилище картинок (тот же токен click.ru). Сервер принимает
# токен и в заголовке `X-Auth-Token`, и в пути `/c/<token>/mcp`; используем заголовок,
# чтобы не светить токен в URL/выводе (единообразно с vk-ads).
KEEPIMAGE_URL = "https://storage.aihub.click.ru/mcp"
KEEPIMAGE_SERVER = "keepimage"


# ---------- пути конфигов ----------

def cursor_user_config() -> Path:
    return Path.home() / ".cursor" / "mcp.json"


def cursor_project_config() -> Path:
    return Path.cwd() / ".cursor" / "mcp.json"


def claude_code_config() -> Path:
    return Path.cwd() / ".mcp.json"


def claude_desktop_config() -> Path | None:
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        return Path(appdata) / "Claude" / "claude_desktop_config.json" if appdata else None
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(xdg) / "Claude" / "claude_desktop_config.json"


TARGETS = {
    "cursor": cursor_user_config,
    "cursor-project": cursor_project_config,
    "claude-code": claude_code_config,
    "claude-desktop": claude_desktop_config,
}


# ---------- заголовки и блоки ----------

def vk_headers(token: str | None, account_id: str | None, user_id: str | None,
               vk_ads_token: str | None) -> dict:
    if vk_ads_token:
        return {"X-VK-Ads-Token": vk_ads_token}
    headers = {"X-Click-Ru-Token": token, "X-Click-Ru-Account-Id": account_id}
    if user_id:
        headers["X-Click-Ru-User-Id"] = user_id
    return headers


def keepimage_headers(token: str, user_id: str | None) -> dict:
    headers = {"X-Auth-Token": token}
    if user_id:
        headers["X-Auth-UserId"] = user_id
    return headers


def build_entry(target: str, url: str, headers: dict) -> dict:
    if target == "claude-desktop":
        args = ["-y", "mcp-remote", url]
        for key, value in headers.items():
            args += ["--header", f"{key}: {value}"]
        return {"command": "npx", "args": args}
    entry = {"url": url, "headers": headers}
    if target == "claude-code":
        entry = {"type": "http", **entry}
    return entry


# ---------- запись конфигов ----------

def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"Не смог распарсить {path}: {e}")


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        print(f"  Бэкап: {backup}")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def mask(value: str) -> str:
    return f"{value[:4]}...{value[-2:]}" if len(value) > 6 else "***"


def mask_entry(entry: dict) -> dict:
    entry = json.loads(json.dumps(entry))
    headers = entry.get("headers")
    if headers:
        for key in headers:
            if "token" in key.lower():
                headers[key] = mask(headers[key])
    if entry.get("args"):
        entry["args"] = [
            arg.split(": ", 1)[0] + ": " + mask(arg.split(": ", 1)[1])
            if ": " in arg and "token" in arg.split(": ", 1)[0].lower()
            else arg
            for arg in entry["args"]
        ]
    return entry


def apply_entries(config_path: Path, entries: dict, *, remove: bool, dry_run: bool) -> None:
    """entries: {server_name: entry}. На --remove значения игнорируются, ключи снимаются."""
    config = load_json(config_path)
    servers = config.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise SystemExit(f"{config_path}: поле mcpServers не объект, правлю вручную не буду")

    if remove:
        for name in entries:
            if servers.pop(name, None) is not None:
                print(f"  - {name}: удалён")
            else:
                print(f"  - {name}: не был записан, пропускаю")
        if not servers:
            config.pop("mcpServers", None)
    else:
        for name, entry in entries.items():
            replaced = name in servers
            servers[name] = entry
            print(f"  - {name}: {'перезаписан' if replaced else 'добавлен'}")
            print(f"    {json.dumps(mask_entry(entry), ensure_ascii=False)}")

    if dry_run:
        print(f"[dry-run] Не пишу в {config_path}.")
        return
    save_json(config_path, config)
    print(f"  Записано: {config_path}")


# ---------- main ----------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Подключить хостовый MCP «VK Реклама» (vkads-mcp.aihub.click.ru) к Cursor, Claude Code, Claude Desktop",
    )
    parser.add_argument("--token", help="API-токен click.ru (или env CLICK_RU_TOKEN)")
    parser.add_argument("--vk-account-id", help="ID аккаунта VK Рекламы в click.ru (или env CLICK_RU_ACCOUNT_ID)")
    parser.add_argument("--click-ru-user-id", help="ID пользователя click.ru — только для мастер-аккаунта")
    parser.add_argument("--vk-ads-token", help="Готовый access_token VK Ads вместо click.ru (или env VK_ADS_ACCESS_TOKEN)")
    parser.add_argument(
        "--target", action="append",
        choices=["cursor", "cursor-project", "claude-code", "claude-desktop", "all"],
        help="Куда писать конфиг. Можно несколько раз. По умолчанию: cursor",
    )
    parser.add_argument("--remove", action="store_true", help="Удалить записи vk-ads и keepimage из конфигов")
    parser.add_argument("--dry-run", action="store_true", help="Показать, что будет записано, без записи")
    parser.add_argument(
        "--no-keepimage", action="store_true",
        help="Не трогать коннектор KeepImage (по умолчанию на пути click.ru он добавляется/снимается вместе с vk-ads)",
    )
    args = parser.parse_args(argv)

    targets = args.target or ["cursor"]
    if "all" in targets:
        targets = ["cursor", "claude-code", "claude-desktop"]

    headers: dict = {}
    if not args.remove:
        vk_ads_token = args.vk_ads_token or os.environ.get("VK_ADS_ACCESS_TOKEN")
        token = args.token or os.environ.get("CLICK_RU_TOKEN")
        account_id = args.vk_account_id or os.environ.get("CLICK_RU_ACCOUNT_ID")
        if not vk_ads_token and not (token and account_id):
            print(
                "Нужны креды. Варианты:\n"
                "  A. click.ru: --token <CLICK_RU_TOKEN> --vk-account-id <ID>\n"
                "     (env CLICK_RU_TOKEN + CLICK_RU_ACCOUNT_ID)\n"
                "  B. готовый токен VK Ads: --vk-ads-token <eyJ0...> (env VK_ADS_ACCESS_TOKEN)",
                file=sys.stderr,
            )
            return 1
        headers = vk_headers(token, account_id, args.click_ru_user_id, vk_ads_token)

    # KeepImage подключаем по пути click.ru (тот же токен). С готовым --vk-ads-token
    # токена click.ru нет — KeepImage не трогаем. Флаг --no-keepimage отключает и на снятии.
    manage_keepimage = not args.no_keepimage
    add_keepimage = manage_keepimage and not args.remove and bool(token) and not vk_ads_token
    kp_headers = keepimage_headers(token, args.click_ru_user_id or os.environ.get("CLICK_RU_USER_ID")) if add_keepimage else {}

    print("=== Хостовый MCP «VK Реклама» ===")
    print(f"Сервер: {VK_ADS_SERVER} → {VK_ADS_URL}")
    if add_keepimage:
        print(f"        {KEEPIMAGE_SERVER} → {KEEPIMAGE_URL} (хранилище картинок, тот же токен click.ru)")
    print(f"Цели:   {', '.join(targets)}")
    print()

    for target in targets:
        path = TARGETS[target]()
        if path is None:
            print(f"[{target}] Не нашёл стандартный путь конфига для {platform.system()}, пропускаю.")
            continue
        print(f"[{target}] {path}")
        if target == "claude-desktop" and not args.remove:
            print("  Формат: stdio-мост npx mcp-remote (нужен Node.js в PATH)")

        if args.remove:
            entries = {VK_ADS_SERVER: None}
            if manage_keepimage:
                entries[KEEPIMAGE_SERVER] = None
        else:
            entries = {VK_ADS_SERVER: build_entry(target, VK_ADS_URL, headers)}
            if add_keepimage:
                entries[KEEPIMAGE_SERVER] = build_entry(target, KEEPIMAGE_URL, kp_headers)

        apply_entries(path, entries, remove=args.remove, dry_run=args.dry_run)
        print()

    # Токен click.ru → реестр ключей, чтобы scripts/upload_creatives_to_storage.py
    # работал сразу после подключения MCP (без отдельного `manage_credentials set clickru`).
    # Только путь click.ru: с готовым --vk-ads-token токен click.ru не используется.
    if not args.remove and token and not vk_ads_token:
        user_id = args.click_ru_user_id or os.environ.get("CLICK_RU_USER_ID")
        if args.dry_run:
            extra = " + clickru_user_id" if user_id else ""
            print(
                f"[dry-run] Токен click.ru был бы сохранён в реестр ключей как "
                f"'clickru'{extra} — для scripts/upload_creatives_to_storage.py."
            )
            print()
        else:
            try:
                set_api_key("clickru", token)
                saved = ["clickru"]
                if user_id:
                    set_api_key("clickru_user_id", user_id)
                    saved.append("clickru_user_id")
                print(
                    f"✓ токен click.ru сохранён в реестр ключей ({', '.join(saved)}, "
                    f"{mask(token)}) — upload_creatives_to_storage.py готов к работе"
                )
            except Exception as e:
                print(
                    f"⚠ конфиг MCP записан, но токен не сохранён в реестр: {e}\n"
                    f"  выполни вручную: python -m scripts.manage_credentials set clickru"
                )
            print()

    if args.remove or args.dry_run:
        return 0

    print("=== Дальше ===")
    if "claude-desktop" in targets:
        print("1. Полностью закрой Claude Desktop (в трее тоже) и открой заново.")
    if "cursor" in targets or "cursor-project" in targets:
        print("1. Cursor: Settings → MCP — сервер vk-ads должен стать зелёным (или перезапусти Cursor).")
    if "claude-code" in targets:
        print("1. Claude Code: новая сессия подхватит .mcp.json автоматически.")
    print("2. Проверь связь: вызови vk_ads_auth_check — должен вернуть данные пользователя VK Ads.")
    if add_keepimage:
        print("   KeepImage: инструмент storage_publish_image (или скрипт upload_creatives_to_storage.py) — заливка картинок.")
    print("3. Ошибка «Не заданы креды» = не дошли заголовки; 401 — токен недействителен.")
    print()
    print("Справочник подключения: docs/hosted-mcp-setup.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
