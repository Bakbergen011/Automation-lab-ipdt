from datetime import datetime, timezone


class AlarmManager:
    def __init__(self, persist=None):
        self.events: list[dict] = []
        self.alarms: list[dict] = []
        self.active: dict[str, dict] = {}
        self.persist = persist

    def event(self, message: str, severity: str = "INFO", source: str = "SYSTEM") -> dict:
        if severity in {"ALARM", "ERROR"} and source in self.active:
            return self.active[source]
        item = {"time": datetime.now(timezone.utc).isoformat(), "severity": severity, "source": source, "message": message}
        self.events.insert(0, item)
        del self.events[500:]
        if self.persist:
            self.persist(item)
        if severity in {"ALARM", "ERROR"}:
            self.alarms.insert(0, item.copy())
            del self.alarms[200:]
            self.active[source] = item
        return item

    def clear(self, source: str, message: str = "Условие восстановлено") -> None:
        if source in self.active:
            del self.active[source]
            self.event(message, "INFO", source)

    def range_check(self, tag: str, value: float, low: float = -50, high: float = 250) -> None:
        if not low <= value <= high:
            self.event(f"{tag}: значение {value:.2f} °C вне диапазона {low}...{high} °C", "ALARM", f"RANGE:{tag}")
        else:
            self.clear(f"RANGE:{tag}", f"{tag}: значение вернулось в допустимый диапазон")