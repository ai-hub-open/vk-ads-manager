@echo off
REM Упаковка vk-ads-manager в .skill (двойной клик в Проводнике)
REM Создаёт файл vk-ads-manager.skill рядом с папкой скилла

cd /d "%~dp0"
python scripts\package_skill.py --skill-path . --output ..
if errorlevel 1 (
    echo.
    echo Ошибка упаковки. Проверь что Python установлен и доступен.
    pause
    exit /b 1
)
echo.
echo Готово. Файл .skill создан в папке-родителе.
pause
