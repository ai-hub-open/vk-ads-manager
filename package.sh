#!/usr/bin/env bash
# Упаковка vk-ads-manager в .skill (запуск: ./package.sh)

cd "$(dirname "$0")"
python3 scripts/package_skill.py --skill-path . --output ..
