import math


def scale_value(raw: float, raw_min: float, raw_max: float, eng_min: float, eng_max: float) -> float:
    if raw_max == raw_min:
        raise ValueError("Границы сырого диапазона не должны совпадать")
    return eng_min + (float(raw) - raw_min) * (eng_max - eng_min) / (raw_max - raw_min)


def decode_registers(registers: list[int], data_type: str = "uint16", byte_order: str = "big") -> float:
    if data_type in {"uint16", "int16"}:
        if len(registers) < 1:
            raise ValueError("Для 16-битного значения нужен один регистр")
        raw = int(registers[0]) & 0xFFFF
        if data_type == "int16" and raw >= 0x8000:
            raw -= 0x10000
        return float(raw)
    if len(registers) < 2:
        raise ValueError("Для 32-битного значения нужны два регистра")
    words = [int(value) & 0xFFFF for value in registers[:2]]
    if byte_order == "little":
        words.reverse()
    raw = (words[0] << 16) | words[1]
    if data_type == "uint32":
        return float(raw)
    if data_type == "int32":
        return float(raw - 0x100000000 if raw >= 0x80000000 else raw)
    if data_type == "float32":
        import struct

        value = struct.unpack(">f", raw.to_bytes(4, "big"))[0]
        if not math.isfinite(value):
            raise ValueError("Регистр содержит нечисловое значение")
        return float(value)
    raise ValueError(f"Неизвестный тип данных: {data_type}")