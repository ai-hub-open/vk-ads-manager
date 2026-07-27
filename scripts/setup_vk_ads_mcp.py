#!/usr/bin/env python3
"""
setup_vk_ads_mcp.py — подключает vk-ads-mcp (https://github.com/ai-hub-open/vk-ads-mcp)
к Claude Desktop одной командой.

Что делает:
1. Находит claude_desktop_config.json для текущей ОС
2. Создаёт/обновляет запись `mcpServers.vk-ads` со ссылкой на установленный vk-ads-mcp
3. Подставляет VK_ADS_ACCESS_TOKEN из credentials.json (если уже сохранён)
   или из аргумента --token

Использование (Claude сам запускает в Cowork):
    # Если уже клонирован vk-ads-mcp и сохранён токен через manage_credentials:
    python -m scripts.setup_vk_ads_mcp \
      --vk-ads-mcp-path C:\\Users\\ptica\\Documents\\vk-ads-mcp

    # С токеном напрямую:
    python -m scripts.setup_vk_ads_mcp \
      --vk-ads-mcp-path C:\\path\\to\\vk-ads-mcp \
      --token eyJ0...

    # Удалить запись (revert):
    python -m scripts.setup_vk_ads_mcp --remove

После запуска — перезапустить Claude Desktop. Tools mcp__vk-ads__* станут доступны в чате.
"""

import argparse
import json
import os
import platform
import shutil
import sys
from pathlib import Path

try:
    from scripts.credentials import load_api_key, CredentialNotFound
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from scripts.credentials import load_api_key, CredentialNotFound


def claude_desktop_config_path() -> Path:
    """Возвращает путь к claude_desktop_config.json для текущей ОС."""
    system = platform.system()
    if system == "Darwin":  # macOS
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Claude" / "claude_desktop_config.json"
        return Path.home() / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    # Linux / другое
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_config(path: Path, data: dict, backup: bool = True):
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        bk = path.with_suffix(".json.bak")
        shutil.copy(path, bk)
        print(f"  Backup: {bk}")
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_token(arg_token: str = None) -> str:
    """Получает VK Ads токен: 1) из аргумента, 2) из credentials.json, 3) raise."""
    if arg_token:
        return arg_token
    try:
        return load_api_key("vk_ads")
    except CredentialNotFound:
        raise SystemExit(
            "Нужен токен VK Ads. Либо передай --token <key>, либо сохрани заранее:\n"
            "  python -m scripts.manage_credentials set vk_ads"
        )


def main():
    parser = argparse.ArgumentParser(description="Настройка vk-ads-mcp в Claude Desktop config")
    parser.add_argument(
        "--vk-ads-mcp-path",
        help="Абсолютный путь к локально клонированному vk-ads-mcp",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="VK Ads access token (если не передан — берётся из credentials)",
    )
    parser.add_argument(
        "--server-name",
        default="vk-ads",
        help="Имя сервера в mcpServers (по умолчанию vk-ads)",
    )
    parser.add_argument("--remove", action="store_true", help="Удалить запись vk-ads из конфига")
    parser.add_argument("--check", action="store_true", help="Только показать текущую конфигурацию")
    args = parser.parse_args()

    cfg_path = claude_desktop_config_path()
    print(f"Claude Desktop config: {cfg_path}")
    config = load_config(cfg_path)

    if args.check:
        servers = config.get("mcpServers", {})
        print(f"\nТекущие MCP-серверы ({len(servers)}):")
        for name, conf in servers.items():
            print(f"  - {name}: {conf.get('command', '?')}")
        return 0

    if args.remove:
        if "mcpServers" not in config or args.server_name not in config["mcpServers"]:
            print(f"Запись '{args.server_name}' не найдена. Ничего не сделано.")
            return 0
        del config["mcpServers"][args.server_name]
        if not config["mcpServers"]:
            del config["mcpServers"]
        save_config(cfg_path, config)
        print(f"✓ Запись '{args.server_name}' удалена. Перезапусти Claude Desktop.")
        return 0

    # Обычный режим — set
    if not args.vk_ads_mcp_path:
        print("ERROR: укажи --vk-ads-mcp-path <путь к клонированному репо>", file=sys.stderr)
        print("\nЕсли репо ещё не клонирован, выполни:", file=sys.stderr)
        print("  git clone https://github.com/ai-hub-open/vk-ads-mcp.git", file=sys.stderr)
        print("  cd vk-ads-mcp && uv sync   # или pip install -e .", file=sys.stderr)
        return 1

    mcp_path = Path(args.vk_ads_mcp_path).resolve()
    if not mcp_path.exists():
        print(f"ERROR: путь {mcp_path} не существует", file=sys.stderr)
        return 1
    if not (mcp_path / "pyproject.toml").exists():
        print(f"WARN: в {mcp_path} нет pyproject.toml — возможно это не корень vk-ads-mcp", file=sys.stderr)

    token = get_token(args.token)

    # Собираем entry для mcpServers
    # Используем uvx --from <path> vk-ads-mcp — это стабильно и не зависит от
    # активного виртуального окружения.
    entry = {
        "command": "uvx",
        "args": ["--from", str(mcp_path), "vk-ads-mcp"],
        "env": {
            "VK_ADS_ACCESS_TOKEN": token,
        },
    }

    if "mcpServers" not in config:
        config["mcpServers"] = {}
    config["mcpServers"][args.server_name] = entry

    save_config(cfg_path, config)
    print(f"\n✓ Запись '{args.server_name}' сохранена.")
    print(f"\nСледующие шаги:")
    print(f"  1. Перезапусти Claude Desktop (полный выход + перезапуск, не reload)")
    print(f"  2. В чате спроси Claude: «Какие у тебя есть mcp__vk-ads__* tools?»")
    print(f"  3. Если показывает 48 инструментов — всё работает!")
    print(f"\nЕсли что-то не так — проверь логи Claude Desktop:")
    print(f"  macOS:   ~/Library/Logs/Claude/")
    print(f"  Windows: %APPDATA%\\Claude\\logs\\")
    return 0


if __name__ == "__main__":
    sys.exit(main())
