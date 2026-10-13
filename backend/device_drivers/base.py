from abc import ABC, abstractmethod


class DeviceDriver(ABC):
    @abstractmethod
    def read_input(self, address: int) -> int: ...

    @abstractmethod
    def read_temperature(self, address: int, data_type: str, scale: float, offset: float) -> float: ...

    @abstractmethod
    def read_holding_register(self, address: int) -> int: ...

    @abstractmethod
    def read_coil(self, address: int) -> bool: ...

    @abstractmethod
    def write_output(self, address: int, value: bool, confirmed: bool = False) -> None: ...

    @abstractmethod
    def get_connection_status(self) -> dict: ...