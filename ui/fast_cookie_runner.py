# ui/fast_cookie_runner.py
"""
Поток-обёртка для FastCookieCollector.
Запускает сбор cookies в отдельном потоке и шлёт сигналы в UI.
"""
import asyncio
import logging
from PyQt5.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


class FastCookieRunner(QThread):
    finished = pyqtSignal(dict)      # {bk: "ok" | "error: ..."}
    progress = pyqtSignal(str)       # текстовый статус

    def __init__(self, profile_id: str, api_key: str,
                 headless: bool, bks: list, parent=None):
        super().__init__(parent)
        self.profile_id = profile_id
        self.api_key = api_key
        self.headless = headless
        self.bks = list(bks)

    def run(self):
        try:
            from core.fast_cookie_collector import FastCookieCollector
            collector = FastCookieCollector(
                profile_id=self.profile_id,
                api_key=self.api_key,
                headless=self.headless,
                progress_cb=lambda msg: self.progress.emit(msg),
            )
            status = asyncio.run(collector.collect_all(self.bks))
            self.finished.emit(status)
        except Exception as e:
            logger.error(f"FastCookieRunner: {e}", exc_info=True)
            self.finished.emit({bk: f"error: {e}" for bk in self.bks})