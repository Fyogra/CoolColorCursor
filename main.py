# -*- coding: utf-8 -*-
"""
CCC | CoolColorCursor
Неоновый шлейф за курсором мыши и помощник для Dota 2 поверх всех окон. Windows, PyQt5. Всё в одном файле.

Что внутри:
    • шлейф за курсором и WinAPI-хелперы оверлея
    • локальный GSI-сервер Dota 2: игровое время и текущий герой
    • таймеры лотуса, рун золота, опыта и усиления под часами игры, колокольчик за 5 с до лотуса
    • сборки на героя по 5 позициям (данные OpenDota, обновление раз в сутки)
    • иконка-кнопка в углу экрана, панель управления, трей, настройки

Сборка в один exe:
    pyinstaller --onefile --noconsole --name main main.py
"""
from __future__ import annotations

import sys
import time
from PyQt5.QtCore import QSharedMemory
from PyQt5.QtWidgets import QApplication
from app.config import APP_NAME, log, setup_logging
from app.control_panel import ControlPanel
from app.updater import UPDATED_ARG


# ──────────────────────────────────────────────────────────────────────────────
def main() -> int:
    setup_logging()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Защита от второго экземпляра (например, автозапуск + ручной запуск)
    guard = QSharedMemory(f"{APP_NAME}_single_instance")
    if guard.attach():
        guard.detach()  # на Unix чистит «осиротевший» сегмент после падения
    # После обновления новый exe стартует, пока старый ещё закрывается, — ждём его до 20 секунд
    tries = 80 if UPDATED_ARG in sys.argv else 1
    while not guard.create(1):
        tries -= 1
        if tries <= 0:
            log.warning("Приложение уже запущено — выход")
            return 0
        time.sleep(0.25)

    panel = ControlPanel()
    panel.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
