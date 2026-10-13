import asyncio
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from backend.alarm_manager import AlarmManager
from backend.configuration import HardwareConfig, RuntimeConfig, validate_hardware, validate_runtime
from backend.device_drivers.modbus import ModbusDriver, available_serial_ports
from backend.historian import Historian
from backend.pid_controller import PIDController, interlock
from backend.signal_processing import scale_value


ROOT = Path(__file__).resolve().parent.parent


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LabState:
    def __init__(self):
        self.config = RuntimeConfig()
        self.hardware = HardwareConfig()
        self.driver: ModbusDriver | None = None
        self.history = Historian(str(ROOT / "data" / "history.sqlite3"))
        self.alarms = AlarmManager(self.history.add_event)
        self.pid = PIDController()
        self.temperatures = [None, None, None]
        self.quality = [0, 0, 0]
        self.analog_raw = [None, None, None]
        self.last_update = None
        self.last_poll = now_iso()
        self.last_tick = datetime.now(timezone.utc)
        self.last_error = ""
        self.last_setpoint = self.config.setpoint
        self.started_at = self.last_tick
        self.rng = random.Random()

    def set_outputs(self, heater: bool, cooler: bool, confirm_write: bool = False) -> None:
        interlock(heater, cooler)
        if self.config.emergency_stop:
            raise PermissionError("Аварийная остановка активна")
        if self.config.mode == "HARDWARE":
            if not heater and not cooler and not self.config.heater and not self.config.cooler:
                return
            if not self.driver or not self.driver.get_connection_status()["connected"]:
                raise ConnectionError("Нет подключения к контроллеру")
            if not self.hardware.write_armed or not confirm_write:
                raise PermissionError("Аппаратная запись не разрешена: требуется настройка и подтверждение оператора")
            if heater and self.hardware.heater_coil is None or cooler and self.hardware.cooler_coil is None:
                raise PermissionError("Не заданы адреса выходных катушек из карты регистров производителя")
            if heater:
                self.driver.write_output(self.hardware.cooler_coil, False, confirmed=True)
                self.driver.write_output(self.hardware.heater_coil, True, confirmed=True)
            elif cooler:
                self.driver.write_output(self.hardware.heater_coil, False, confirmed=True)
                self.driver.write_output(self.hardware.cooler_coil, True, confirmed=True)
            elif self.hardware.heater_coil is not None and self.hardware.cooler_coil is not None:
                self.driver.write_output(self.hardware.heater_coil, False, confirmed=True)
                self.driver.write_output(self.hardware.cooler_coil, False, confirmed=True)
        self.config.heater = heater
        self.config.cooler = cooler

    def _control(self, dt: float) -> None:
        if self.config.emergency_stop:
            return
        if not self.config.running:
            if self.config.mode == "HARDWARE":
                if self.hardware.write_armed and (self.config.heater or self.config.cooler):
                    self.set_outputs(False, False, confirm_write=True)
                return
            self.set_outputs(False, False, confirm_write=self.config.mode == "HARDWARE")
            return
        measured = self.temperatures[0]
        heater = cooler = False
        if self.config.control_mode == "MANUAL":
            heater = self.config.action == "HEAT"
            cooler = self.config.action == "COOL"
        elif self.config.control_mode == "PID":
            if measured < self.config.setpoint:
                heater = self.pid.update(self.config.setpoint, measured, dt) > 2
            elif measured > self.config.setpoint:
                cooler = self.pid.update(measured, self.config.setpoint, dt) > 2
            else:
                self.pid.reset()
        else:
            if measured < self.config.setpoint - self.config.hysteresis / 2:
                heater = True
            elif measured > self.config.setpoint + self.config.hysteresis / 2:
                cooler = True
        self.set_outputs(heater, cooler, confirm_write=self.config.mode == "HARDWARE")

    def sample(self) -> dict:
        now = datetime.now(timezone.utc)
        dt = max(0.1, min((now - self.last_tick).total_seconds(), 5.0))
        self.last_tick = now
        try:
            self._control(dt)
            if self.config.mode == "SIMULATION":
                if self.temperatures[0] is None:
                    self.temperatures = [22.1, 21.8, 21.6]
                    self.quality = [98, 97, 99]
                ambient = 22.0
                target1 = ambient + (34 if self.config.heater else 0) - (19 if self.config.cooler else 0)
                target2 = self.temperatures[0] + (7 if self.config.heater else 0) - (3 if self.config.cooler else 0)
                target3 = self.temperatures[0] - (2 if self.config.heater else 0) - (6 if self.config.cooler else 0)
                targets = [target1, target2, target3]
                delays = [42.0, 17.0, 28.0]
                for index, target in enumerate(targets):
                    self.temperatures[index] += (target - self.temperatures[index]) * min(1, dt / delays[index])
                    self.temperatures[index] += self.rng.gauss(0, 0.025)
                    self.quality[index] = max(92, min(100, round(98 + self.rng.gauss(0, 1))))
                self.analog_raw = [4 + value / 100 * 16 for value in self.temperatures]
                self.last_error = ""
                self.last_update = now_iso()
            elif self.driver:
                self.temperatures = [self.driver.read_temperature(address, self.hardware.data_type, self.hardware.scale, self.hardware.offset) for address in self.hardware.registers]
                self.quality = [100, 100, 100]
                self.analog_raw = [self.driver.last_raw[address] for address in self.hardware.registers]
                if self.hardware.heater_coil is not None:
                    self.config.heater = self.driver.read_coil(self.hardware.heater_coil)
                if self.hardware.cooler_coil is not None:
                    self.config.cooler = self.driver.read_coil(self.hardware.cooler_coil)
                if self.config.heater and self.config.cooler:
                    self.alarms.event("Оба аппаратных выхода включены одновременно; проверьте внешнюю логику ПЛК", "ALARM", "SAFETY")
                else:
                    self.alarms.clear("SAFETY", "Аппаратные выходы не включены одновременно")
                self.last_error = ""
                self.alarms.clear("CONTROLLER", "Связь с контроллером восстановлена")
                self.last_update = now_iso()
            else:
                raise ConnectionError("Нет подключения к устройству. Настройте Modbus и проверьте соединение.")
        except Exception as exc:
            self.last_error = str(exc)
            if self.config.mode == "HARDWARE":
                self.config.heater = None
                self.config.cooler = None
                self.config.running = False
            else:
                self.config.heater = False
                self.config.cooler = False
            self.alarms.event(f"Потеря связи / ошибка опроса: {self.last_error}", "ERROR", "CONTROLLER")
        self.last_poll = now_iso()
        for index, value in enumerate(self.temperatures):
            if value is not None and math.isfinite(value):
                self.alarms.range_check(f"T{index + 1}", value)
        sample = self.snapshot()
        if all(value is not None for value in self.temperatures):
            sample.update({"t1": self.temperatures[0], "t2": self.temperatures[1], "t3": self.temperatures[2], "quality": "GOOD" if not self.last_error else "BAD"})
            self.history.add_sample(sample)
        if self.config.setpoint != self.last_setpoint:
            self.history.add_setpoint(self.last_update, self.config.setpoint)
            self.alarms.event(f"Уставка изменена: {self.last_setpoint:.1f} → {self.config.setpoint:.1f} °C", source="SETPOINT")
            self.last_setpoint = self.config.setpoint
        return self.snapshot()

    def snapshot(self) -> dict:
        connected = self.config.mode == "SIMULATION" or bool(self.driver and self.driver.get_connection_status()["connected"])
        return {
            "mode": self.config.mode, "simulated": self.config.mode == "SIMULATION",
            "temperatures": [{"tag": f"T{i + 1}", "value": round(value, 2) if value is not None else None, "unit": "°C", "status": "НЕТ СВЯЗИ" if not connected else "НЕТ ДАННЫХ" if value is None else "НОРМА" if -50 <= value <= 250 else "ВНЕ ДИАПАЗОНА", "quality": self.quality[i] if connected else 0, "raw": round(self.analog_raw[i], 3) if self.analog_raw[i] is not None else None} for i, value in enumerate(self.temperatures)],
            "setpoint": self.config.setpoint, "hysteresis": self.config.hysteresis,
            "control_mode": self.config.control_mode, "action": self.config.action,
            "running": self.config.running, "emergency_stop": self.config.emergency_stop,
            "heater": self.config.heater, "cooler": self.config.cooler,
            "time": self.last_update, "last_update": self.last_update, "last_poll": self.last_poll, "poll_ms": self.config.poll_ms,
            "connected": connected, "connection_error": self.last_error,
            "write_enabled": self.hardware.write_armed and self.hardware.heater_coil is not None and self.hardware.cooler_coil is not None,
            "device": self.hardware.selected_device if self.config.mode == "HARDWARE" else "Виртуальный стенд",
        }


state = LabState()
app = FastAPI(title="Temperature Automation LAB", version="1.0.0")
app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")


async def poll_loop():
    while True:
        sample = await asyncio.to_thread(state.sample)
        for socket in list(app.state.sockets):
            try:
                await socket.send_json(sample)
            except Exception:
                app.state.sockets.discard(socket)
        await asyncio.sleep(state.config.poll_ms / 1000)


@app.on_event("startup")
async def startup():
    app.state.sockets = set()
    app.state.poller = asyncio.create_task(poll_loop())


@app.on_event("shutdown")
async def shutdown():
    app.state.poller.cancel()
    if state.driver:
        state.driver.close()


@app.get("/", response_class=HTMLResponse)
async def home():
    return (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


@app.get("/api/state")
async def get_state():
    return state.snapshot()


@app.get("/api/ports")
async def get_ports():
    return {"ports": available_serial_ports()}


@app.put("/api/config")
async def update_config(payload: dict):
    try:
        validate_runtime(payload)
        for key, value in payload.items():
            setattr(state.config, key, value)
        return state.snapshot()
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.put("/api/hardware")
async def update_hardware(payload: dict):
    try:
        if state.config.running or state.config.heater or state.config.cooler:
            raise ValueError("Остановите контур перед изменением настроек оборудования")
        validate_hardware(payload)
        confirm = payload.pop("confirm_text", "")
        payload["write_armed"] = bool(payload.get("write_armed")) and confirm == "ENABLE OUTPUT WRITES"
        hardware = HardwareConfig(**{**state.hardware.__dict__, **payload})
        state.hardware = hardware
        if state.driver:
            state.driver.close()
        state.driver = ModbusDriver(hardware.__dict__)
        if hardware.write_armed:
            state.alarms.event("Оператор явно разрешил запись выходов", "WARNING", "SAFETY")
        return {"hardware": hardware.__dict__, "ports": available_serial_ports()}
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/connect")
async def connect_hardware():
    if not state.hardware.selected_device:
        raise HTTPException(400, "Сначала выберите устройство и задайте карту регистров производителя")
    if not state.driver:
        state.driver = ModbusDriver(state.hardware.__dict__)
    try:
        await asyncio.to_thread(state.driver.connect)
        state.config.mode = "HARDWARE"
        state.config.running = False
        state.config.heater = await asyncio.to_thread(state.driver.read_coil, state.hardware.heater_coil) if state.hardware.heater_coil is not None else None
        state.config.cooler = await asyncio.to_thread(state.driver.read_coil, state.hardware.cooler_coil) if state.hardware.cooler_coil is not None else None
        state.alarms.event(f"Подключено устройство: {state.hardware.selected_device}", source="CONTROLLER")
        return state.snapshot()
    except Exception as exc:
        state.alarms.event(f"Подключение не установлено: {exc}", "ERROR", "CONTROLLER")
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/simulation")
async def enable_simulation():
    if state.driver:
        state.driver.close()
    state.config.mode = "SIMULATION"
    state.config.running = False
    state.config.heater = state.config.cooler = False
    state.config.emergency_stop = False
    state.temperatures = [22.1, 21.8, 21.6]
    state.quality = [98, 97, 99]
    state.analog_raw = [4 + value / 100 * 16 for value in state.temperatures]
    state.last_update = now_iso()
    state.alarms.event("Включён режим SIMULATION; показания не являются реальными", source="MODE")
    return state.snapshot()


@app.post("/api/control")
async def control(payload: dict):
    try:
        if payload.get("command") == "start":
            if state.config.mode == "HARDWARE" and payload.get("confirm_text") != "START HARDWARE CONTROL":
                raise PermissionError("Для запуска оборудования введите START HARDWARE CONTROL")
            if state.config.mode == "HARDWARE" and not state.hardware.write_armed:
                raise PermissionError("HARDWARE подключён только для чтения; сначала настройте и явно разрешите выходы")
            if state.config.mode == "HARDWARE" and (state.hardware.heater_coil is None or state.hardware.cooler_coil is None):
                raise PermissionError("Не заданы оба адреса выходных катушек из карты производителя")
            if state.config.mode == "HARDWARE" and (not state.driver or not state.driver.get_connection_status()["connected"]):
                raise ConnectionError("Нет подключения к контроллеру")
            if state.config.emergency_stop:
                raise PermissionError("Сбросьте аварийную остановку перед запуском")
            state.config.running = True
            state.alarms.event("Контур регулирования запущен оператором", source="CONTROL")
        elif payload.get("command") == "stop":
            state.config.running = False
            state.set_outputs(False, False, confirm_write=state.config.mode == "HARDWARE")
            state.alarms.event("Штатная остановка", source="CONTROL")
        elif payload.get("command") == "estop":
            state.config.emergency_stop = True
            state.config.running = False
            if state.config.mode != "HARDWARE" or state.hardware.write_armed:
                state.config.emergency_stop = False
                state.set_outputs(False, False, confirm_write=state.config.mode == "HARDWARE")
                state.config.emergency_stop = True
            state.alarms.event("АВАРИЙНАЯ ОСТАНОВКА активирована; программная команда не заменяет аппаратную цепь безопасности", "ALARM", "SAFETY")
        elif payload.get("command") == "reset_estop":
            state.config.heater = state.config.cooler = False
            state.config.emergency_stop = False
            state.alarms.clear("SAFETY", "Аварийная остановка сброшена оператором")
            state.alarms.event("Аварийная остановка сброшена оператором", "WARNING", "SAFETY")
        else:
            raise ValueError("Неизвестная команда управления")
        return state.snapshot()
    except (ValueError, PermissionError, ConnectionError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/history")
async def get_history(seconds: int = 3600, start: str | None = Query(default=None, alias="from"), end: str | None = Query(default=None, alias="to")):
    if start is not None or end is not None:
        if start is None or end is None:
            raise HTTPException(400, "Для произвольного интервала задайте обе границы")
        try:
            start_time = datetime.fromisoformat(start).astimezone(timezone.utc).isoformat()
            end_time = datetime.fromisoformat(end).astimezone(timezone.utc).isoformat()
        except ValueError as exc:
            raise HTTPException(400, "Некорректный формат даты интервала") from exc
        if start_time >= end_time:
            raise HTTPException(400, "Начало интервала должно быть раньше окончания")
        rows = state.history.between(start_time, end_time)
        return [row for row in rows if row["mode"] == state.config.mode]
    rows = state.history.recent(100000)
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(60, min(seconds, 86400)))
    selected = []
    for row in rows:
        try:
            if datetime.fromisoformat(row["time"]) >= cutoff:
                selected.append(row)
        except ValueError:
            continue
    return [row for row in selected if row["mode"] == state.config.mode]


@app.get("/api/history.csv")
async def export_history():
    return Response(state.history.export_csv(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=temperature-history.csv"})


@app.get("/api/io")
async def get_io():
    connected = state.snapshot()["connected"]
    result = []
    for index, sensor in enumerate(state.snapshot()["temperatures"]):
        address = state.hardware.registers[index] if state.config.mode == "HARDWARE" and index < len(state.hardware.registers) else "—" if state.config.mode == "HARDWARE" else f"SIM-AI-{index + 1}"
        raw = sensor["raw"]
        engineering = scale_value(raw, 4, 20, 0, 100) if state.config.mode == "SIMULATION" else sensor["value"]
        sensor_status = sensor["status"]
        result.append({"type": "AI", "address": address, "signal": "4–20 mA · SIMULATED" if state.config.mode == "SIMULATION" else f"Modbus {state.hardware.register_type}", "raw": raw if connected else "—", "engineering": round(engineering, 2) if connected else "—", "unit": "°C", "status": sensor_status, "error": "" if connected else state.last_error, "last_poll": state.last_poll})
    for channel, value in (("DO-HEAT", state.config.heater), ("DO-COOL", state.config.cooler)):
        address = channel if state.config.mode == "SIMULATION" else state.hardware.heater_coil if channel.endswith("HEAT") else state.hardware.cooler_coil
        if state.config.mode == "HARDWARE" and not connected:
            raw_value, engineering_value, status, error = "—", "—", "ОШИБКА СВЯЗИ", state.last_error
        elif state.config.mode == "HARDWARE" and address is None:
            raw_value, engineering_value, status, error = "—", "—", "НЕ НАСТРОЕН", "Адрес катушки не задан"
        elif state.config.mode == "HARDWARE":
            try:
                raw_value = int(state.driver.read_coil(address))
                engineering_value, status, error = "ВКЛ" if raw_value else "ОТКЛ", "НОРМА", ""
            except Exception as exc:
                raw_value, engineering_value, status, error = "—", "—", "ОШИБКА ЧТЕНИЯ", str(exc)
        else:
            raw_value, engineering_value, status, error = int(value), "ВКЛ" if value else "ОТКЛ", "НОРМА", ""
        result.append({"type": "DO", "address": address, "signal": "Релейный выход" if state.config.mode == "SIMULATION" else "Modbus coil", "raw": raw_value, "engineering": engineering_value, "unit": "", "status": status, "error": error, "last_poll": state.last_poll})
    hardware_unmapped = state.config.mode == "HARDWARE"
    result.extend([{"type": "DI", "address": "DI-ESTOP" if not hardware_unmapped else "—", "signal": "SIMULATED" if not hardware_unmapped else "Не настроен", "raw": int(state.config.emergency_stop) if not hardware_unmapped else "—", "engineering": ("СРАБОТАЛ" if state.config.emergency_stop else "НОРМА") if not hardware_unmapped else "—", "unit": "", "status": "НОРМА" if not hardware_unmapped else "НЕ НАСТРОЕН", "error": "" if not hardware_unmapped else "Адрес дискретного входа не задан", "last_poll": state.last_poll}, {"type": "AO", "address": "AO-CTRL" if not hardware_unmapped else "—", "signal": "SIMULATED" if not hardware_unmapped else "Не настроен", "raw": (100 if state.config.heater or state.config.cooler else 0) if not hardware_unmapped else "—", "engineering": ("100 %" if state.config.heater or state.config.cooler else "0 %") if not hardware_unmapped else "—", "unit": "%", "status": "НОРМА" if not hardware_unmapped else "НЕ НАСТРОЕН", "error": "" if not hardware_unmapped else "Адрес аналогового выхода не задан", "last_poll": state.last_poll}])
    return result


@app.get("/api/events")
async def get_events():
    events = state.history.recent_events(100)
    alarm_history = [event for event in state.history.recent_events(200) if event["severity"] in {"ALARM", "ERROR"}]
    return {"events": events, "alarms": list(state.alarms.active.values()), "alarm_history": alarm_history, "setpoints": state.history.recent_setpoints(100)}


@app.websocket("/ws")
async def websocket_updates(websocket: WebSocket):
    await websocket.accept()
    app.state.sockets.add(websocket)
    try:
        await websocket.send_json(state.snapshot())
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        app.state.sockets.discard(websocket)