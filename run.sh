#!/bin/bash
# ============================================================
# NEXUS Run Script — быстрый запуск
# ============================================================

cd "$(dirname "$0")" || exit 1

# Ищем venv
if [ -d ".venv1" ]; then
    # shellcheck source=/dev/null
    source .venv1/bin/activate
elif [ -d ".venv" ]; then
    # shellcheck source=/dev/null
    source .venv/bin/activate
else
    echo "❌ venv не найден. Сначала запусти: ./install.sh"
    exit 1
fi

# Проверка импорта
if ! python3 -c "import nexus" 2>/dev/null; then
    echo "❌ NEXUS не установлен корректно."
    echo "   Перезапусти: ./install.sh"
    exit 1
fi

# Запуск
exec python3 -m nexus "$@"