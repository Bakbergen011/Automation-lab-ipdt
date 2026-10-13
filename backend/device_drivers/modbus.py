from datetime import datetime, timezone

from backend.device_drivers.base import DeviceDriver
from backend.signal_processing import decode_registers


def available_serial_ports() -> list[dict]:
    try:
        from serial.tools import list_ports

        return [{"port": port.device, "description": port.description, "hardware_id": port.hwid} for port in list_ports.comports()]
    except ImportError:
        return []


class ModbusDriver(DeviceDriver):
    def __init__(self, config: dict):
        self.config = config
        self.client = None
        self.connected = False
        self.last_error = ""
        self.last_poll = None
        self.last_raw: dict[int, float] = {}

    def connect(self) -> None:
        try:
            from pymodbus.client import ModbusSerialClient, ModbusTcpClient
        except ImportError as exc:
            raise RuntimeError("Для Modbus требуется установить зависимости из requirements.txt") from exc
        if self.config["transport"] == "TCP":
            self.client = ModbusTcpClient(self.config["host"], port=int(self.config["port"]), timeout=2)
        else:
            self.client = ModbusSerialClient(
                port=self.config["com_port"], baudrate=int(self.config["baudrate"]),
                parity=self.config.get("parity", "N"), stopbits=int(self.config.get("stopbits", 1)),
                bytesize=8, timeout=2,
            )
        self.connected = bool(self.client.connect())
        self.last_error = "" if self.connected else "Не удалось установить соединение"
        if not self.connected:
            raise ConnectionError(self.last_error)

    def close(self) -> None:
        if self.client:
            self.client.close()
        self.connected = False

    def _call(self, method: str, *args, **kwargs):
        if not self.client or not self.connected:
            raise ConnectionError("Modbus-устройство не подключено")
        function = getattr(self.client, method)
        try:
            try:
                result = function(*args, device_id=int(self.config["slave_id"]), **kwargs)
            except TypeError:
                result = function(*args, slave=int(self.config["slave_id"]), **kwargs)
            if result.isError():
                raise IOError(f"Ошибка Modbus: {result}")
            self.last_poll = datetime.now(timezone.utc).isoformat()
            self.last_error = ""
            return result
        except Exception as exc:
            self.last_error = str(exc)
            raise

    def read_input(self, address: int) -> int:
        return int(self._call("read_input_registers", address, count=1).registers[0])

    def read_holding_register(self, address: int) -> int:
        return int(self._call("read_holding_registers", address, count=1).registers[0])

    def read_temperature(self, address: int, data_type: str, scale: float, offset: float) -> float:
        count = 1 if data_type in {"uint16", "int16"} else 2
        method = "read_input_registers" if self.config.get("register_type") == "input" else "read_holding_registers"
        registers = self._call(method, address, count=count).registers
        value = decode_registers(registers, data_type, self.config.get("byte_order", "big"))
        self.last_raw[address] = value
        return value * scale + offset

    def read_coil(self, address: int) -> bool:
        return bool(self._call("read_coils", address, count=1).bits[0])

    def write_output(self, address: int, value: bool, confirmed: bool = False) -> None:
        if not confirmed or not self.config.get("write_armed") or not self.config.get("selected_device"):
            raise PermissionError("Запись заблокирована: выберите устройство, настройте адреса и подтвердите разрешение")
        self._call("write_coil", address, bool(value))

    def get_connection_status(self) -> dict:
        return {"connected": self.connected, "error": self.last_error, "last_poll": self.last_poll}