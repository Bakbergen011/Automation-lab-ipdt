import csv
import io
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Historian:
    def __init__(self, path: str = "data/history.sqlite3"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS samples (time TEXT, t1 REAL, t2 REAL, t3 REAL, setpoint REAL, heater INTEGER, cooler INTEGER, mode TEXT, quality TEXT)")
            db.execute("CREATE INDEX IF NOT EXISTS sample_time_idx ON samples(time)")
            db.execute("CREATE TABLE IF NOT EXISTS setpoint_history (time TEXT, value REAL)")
            db.execute("CREATE TABLE IF NOT EXISTS events (time TEXT, severity TEXT, source TEXT, message TEXT)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def add_sample(self, sample: dict) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO samples VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (sample["time"], sample["t1"], sample["t2"], sample["t3"], sample["setpoint"], int(sample["heater"]), int(sample["cooler"]), sample["mode"], sample["quality"]))

    def add_setpoint(self, time: str, value: float) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO setpoint_history VALUES (?, ?)", (time, value))

    def add_event(self, event: dict) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO events VALUES (?, ?, ?, ?)", (event["time"], event["severity"], event["source"], event["message"]))

    def recent_events(self, limit: int = 100) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT time,severity,source,message FROM events ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return [dict(zip(("time", "severity", "source", "message"), row)) for row in rows]

    def recent_setpoints(self, limit: int = 100) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT time,value FROM setpoint_history ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return [{"time": row[0], "value": row[1]} for row in rows]

    def recent(self, limit: int = 1800) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT time,t1,t2,t3,setpoint,heater,cooler,mode,quality FROM samples ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return [dict(zip(("time", "t1", "t2", "t3", "setpoint", "heater", "cooler", "mode", "quality"), row)) for row in reversed(rows)]

    def between(self, start: str, end: str, limit: int = 200000) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT time,t1,t2,t3,setpoint,heater,cooler,mode,quality FROM samples WHERE time >= ? AND time <= ? ORDER BY time LIMIT ?", (start, end, limit)).fetchall()
        return [dict(zip(("time", "t1", "t2", "t3", "setpoint", "heater", "cooler", "mode", "quality"), row)) for row in rows]

    def export_csv(self) -> str:
        output = io.StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow(["Время", "T1, °C", "T2, °C", "T3, °C", "Уставка, °C", "Нагрев", "Охлаждение", "Режим", "Качество"])
        writer.writerows((row["time"], row["t1"], row["t2"], row["t3"], row["setpoint"], row["heater"], row["cooler"], row["mode"], row["quality"]) for row in self.recent(100000))
        return "\ufeff" + output.getvalue()