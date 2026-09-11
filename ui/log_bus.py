# ui/log_bus.py
"""
Централизованная шина логов.
Любой модуль может отправить лог через log_bus.info/success/warning/error/signal,
MainWindow подписывается на сигнал `entry` и пересылает в LogsPage.
"""
from datetime import datetime
from PyQt5.QtCore import QObject, pyqtSignal


class LogBus(QObject):
    entry = pyqtSignal(dict)
    # level: info | success | warning | error | signal

    def log(self, level: str, source: str, message: str, details: dict = None):
        self.entry.emit({
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "source": source,
            "message": message,
            "details": details or {},
        })

    def info(self, source: str, message: str, details: dict = None):
        self.log("info", source, message, details)

    def success(self, source: str, message: str, details: dict = None):
        self.log("success", source, message, details)

    def warning(self, source: str, message: str, details: dict = None):
        self.log("warning", source, message, details)

    def error(self, source: str, message: str, details: dict = None):
        self.log("error", source, message, details)

    def signal(self, source: str, message: str, details: dict = None):
        self.log("signal", source, message, details)


log_bus = LogBus()