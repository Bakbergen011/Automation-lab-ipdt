from dataclasses import dataclass, field
import math


@dataclass
class RuntimeConfig:
    mode: str = "HARDWARE"
    poll_ms: int = 1000
    setpoint: float = 45.0
    hysteresis: float = 1.0
    control_mode: str = "AUTO"
    action: str = "AUTO"
    running: bool = False
    emergency_stop: bool = False
    heater: bool = False
    cooler: bool = False
    output_limit: float = 100.0


@dataclass
class HardwareConfig:
    transport: str = "RTU"
    host: str = "192.168.0.10"
    port: int = 502
    com_port: str = "COM2"
    baudrate: int = 9600
    parity: str = "N"
    stopbits: int = 1
    slave_id: int = 1
    registers: list[int] = field(default_factory=list)
    register_type: str = "holding"
    data_type: str = "uint16"
    byte_order: str = "big"
    scale: float = 1.0
    offset: float = 0.0
    selected_device: str = ""
    write_armed: bool = False
    heater_coil: int | None = None
    cooler_coil: int | None = None


def validate_runtime(payload: dict) -> dict:
    allowed = {"poll_ms", "setpoint", "hysteresis", "control_mode", "action"}
    if not set(payload).issubset(allowed):
        raise ValueError("Через эту команду разрешено менять только уставку, опрос и режим регулирования")
    if "poll_ms" in payload and not 250 <= int(payload["poll_ms"]) <= 60000:
        raise ValueError("Период опроса должен быть от 250 до 60000 мс")
    if "setpoint" in payload and not -50 <= float(payload["setpoint"]) <= 250:
        raise ValueError("Уставка вне диапазона -50...250 °C")
    if "hysteresis" in payload and not 0.1 <= float(payload["hysteresis"]) <= 20:
        raise ValueError("Гистерезис должен быть от 0.1 до 20 °C")
    if "control_mode" in payload and payload["control_mode"] not in {"AUTO", "MANUAL", "PID"}:
        raise ValueError("Неизвестный режим регулирования")
    if "action" in payload and payload["action"] not in {"AUTO", "HEAT", "COOL", "OFF"}:
        raise ValueError("Неизвестное действие исполнительного механизма")
    return payload


def validate_hardware(payload: dict) -> dict:
    transport = payload.get("transport")
    if transport not in {"TCP", "RTU"}:
        raise ValueError("Поддерживаются Modbus TCP и Modbus RTU")
    if transport == "TCP":
        if not payload.get("host", "").strip():
            raise ValueError("Укажите IP-адрес контроллера из его конфигурации")
        if not 1 <= int(payload.get("port", 0)) <= 65535:
            raise ValueError("Укажите корректный TCP-порт контроллера")
    else:
        if not payload.get("com_port", "").strip():
            raise ValueError("Выберите обнаруженный COM-порт")
        if not 300 <= int(payload.get("baudrate", 0)) <= 921600:
            raise ValueError("Укажите скорость линии из документации контроллера")
        if payload.get("parity") not in {"N", "E", "O"}:
            raise ValueError("Укажите чётность линии из документации контроллера")
        if int(payload.get("stopbits", 0)) not in {1, 2}:
            raise ValueError("Укажите количество стоп-битов из документации контроллера")
    if not 1 <= int(payload.get("slave_id", 0)) <= 247:
        raise ValueError("Укажите Slave ID из документации контроллера")
    if payload.get("register_type") not in {"holding", "input"}:
        raise ValueError("Тип регистра: holding или input")
    if payload.get("data_type") not in {"uint16", "int16", "uint32", "int32", "float32"}:
        raise ValueError("Неподдерживаемый тип данных регистра")
    if payload.get("byte_order") not in {"big", "little"}:
        raise ValueError("Порядок байтов: big или little")
    if "scale" not in payload or not math.isfinite(float(payload["scale"])) or float(payload["scale"]) == 0:
        raise ValueError("Коэффициент масштабирования должен быть конечным и ненулевым")
    if "offset" not in payload or not math.isfinite(float(payload["offset"])):
        raise ValueError("Смещение должно быть конечным числом")
    if not payload.get("selected_device", "").strip():
        raise ValueError("Укажите модель контроллера/измерительного модуля")
    addresses = payload.get("registers", [])
    if len(addresses) != 3 or any(not 0 <= int(address) <= 65535 for address in addresses):
        raise ValueError("Укажите три адреса регистров T1, T2 и T3 из карты производителя")
    if payload.get("data_type", "uint16") in {"uint32", "int32", "float32"} and any(int(address) == 65535 for address in addresses):
        raise ValueError("32-битный регистр не может начинаться по адресу 65535")
    if payload.get("write_armed") and not payload.get("selected_device", "").strip():
        raise ValueError("Для разрешения записи сначала выберите устройство")
    if payload.get("write_armed"):
        if payload.get("confirm_text") != "ENABLE OUTPUT WRITES":
            raise ValueError("Для записи введите ENABLE OUTPUT WRITES")
        coils = [payload.get("heater_coil"), payload.get("cooler_coil")]
        if any(value is None or not 0 <= int(value) <= 65535 for value in coils) or int(coils[0]) == int(coils[1]):
            raise ValueError("Для записи нужны два различных адреса катушек из карты производителя")
    return payload