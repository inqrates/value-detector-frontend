# ui/log_bus.py
from datetime import datetime
from typing import Optional, List
from PyQt5.QtCore import QObject, pyqtSignal


class LogBus(QObject):
    entry = pyqtSignal(dict)

    def log(self, level: str, source: str, message: str,
            details: dict = None,
            bk: Optional[str] = None,
            match_id: Optional[str] = None,
            level3: bool = False):
        self.entry.emit({
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "source": source,
            "message": message,
            "details": details or {},
            "bk": bk,
            "match_id": match_id,
            "level3": bool(level3),
        })

    def info(self, source, message, details=None):
        self.log("info", source, message, details)

    def success(self, source, message, details=None):
        self.log("success", source, message, details)

    def warning(self, source, message, details=None):
        self.log("warning", source, message, details)

    def error(self, source, message, details=None):
        self.log("error", source, message, details)

    def signal(self, source, message, details=None):
        self.log("signal", source, message, details)

    def match_event(
        self,
        bk: str,
        match_id: str,
        teams: List[str],
        message: str,
        level: str = "info",
        level3: bool = False,
        details: dict = None,
    ):
        bk_norm = (bk or "").lower()
        teams = teams or ["?", "?"]
        match_str = f"{teams[0]} vs {teams[1]}" if len(teams) >= 2 else str(teams)
        full_message = f"{match_str} · {message}"

        d = dict(details or {})
        d.setdefault("teams", list(teams))

        self.log(
            level=level,
            source=bk_norm.upper() if bk_norm else "Матч",
            message=full_message,
            details=d,
            bk=bk_norm or None,
            match_id=str(match_id) if match_id else None,
            level3=level3,
        )


log_bus = LogBus()