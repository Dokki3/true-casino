"""Точка входа честного казино.

    python main.py

Сессия живёт только в памяти: при каждом запуске снова $10 000, никакой записи
на диск, никакого доната и никаких реальных денег.
"""

from __future__ import annotations

import sys


def main() -> int:
    try:
        from shell import Casino
    except ImportError as error:
        print("Не найден pygame. Установите его командой:")
        print("    python -m pip install pygame-ce")
        print(f"(подробности: {error})")
        return 1
    Casino().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
