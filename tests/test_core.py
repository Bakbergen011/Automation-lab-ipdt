import asyncio
import tempfile
import unittest
from pathlib import Path

from backend.alarm_manager import AlarmManager
from backend.app import get_io
from backend.configuration import RuntimeConfig, validate_hardware, validate_runtime
from backend.historian import Historian
from backend.pid_controller import PIDController, interlock
from backend.signal_processing import decode_registers, scale_value


class SignalProcessingTests(unittest.TestCase):
    def test_current_loop_scaling(self):
        self.assertEqual(scale_value(12, 4, 20, 0, 100), 50)

    def test_signed_and_float_register_decoding(self):
        self.assertEqual(decode_registers([65535], "int16"), -1)
        self.assertEqual(decode_registers([0x3F80, 0], "float32"), 1.0)

    def test_scaling_rejects_zero_span(self):
        with self.assertRaises(ValueError):
            scale_value(10, 4, 4, 0, 100)


class SafetyTests(unittest.TestCase):
    def test_default_runtime_mode_is_hardware(self):
        self.assertEqual(RuntimeConfig().mode, "HARDWARE")

    def test_heater_and_cooler_interlock(self):
        with self.assertRaises(ValueError):
            interlock(True, True)

    def test_pid_output_is_bounded(self):
        controller = PIDController(kp=10)
        self.assertEqual(controller.update(100, 0, 1), 100)

    def test_hardware_cannot_guess_a_register_map(self):
        with self.assertRaises(ValueError):
            validate_hardware({"selected_device": "unknown PLC"})

    def test_hardware_requires_explicit_line_and_register_settings(self):
        with self.assertRaises(ValueError):
            validate_hardware({"transport": "RTU", "com_port": "COM2", "selected_device": "module", "registers": [1, 2, 3]})

    def test_unconfigured_hardware_io_is_reported_without_fake_values(self):
        rows = asyncio.run(get_io())
        self.assertEqual(rows[0]["address"], "—")
        self.assertEqual(rows[0]["status"], "НЕТ СВЯЗИ")
        self.assertEqual(rows[0]["engineering"], "—")

    def test_writes_require_confirmed_device_and_documented_coils(self):
        config = {"transport": "RTU", "com_port": "COM2", "baudrate": 19200, "parity": "E", "stopbits": 1, "slave_id": 7, "register_type": "holding", "data_type": "int16", "byte_order": "big", "scale": 0.1, "offset": 0, "selected_device": "PLC model", "registers": [10, 11, 12], "write_armed": True, "confirm_text": "ENABLE OUTPUT WRITES", "heater_coil": 4, "cooler_coil": 5}
        self.assertIs(validate_hardware(config), config)
        config["cooler_coil"] = 4
        with self.assertRaises(ValueError):
            validate_hardware(config)

    def test_runtime_rejects_invalid_poll_period(self):
        with self.assertRaises(ValueError):
            validate_runtime({"poll_ms": 100})

    def test_runtime_settings_cannot_override_output_safety_state(self):
        with self.assertRaises(ValueError):
            validate_runtime({"running": True, "heater": True})


class AlarmAndHistorianTests(unittest.TestCase):
    def test_alarm_is_latched_once_and_cleared_on_recovery(self):
        manager = AlarmManager()
        manager.range_check("T1", 300)
        manager.range_check("T1", 301)
        self.assertEqual(len(manager.active), 1)
        self.assertEqual(len(manager.alarms), 1)
        manager.range_check("T1", 20)
        self.assertEqual(len(manager.active), 0)

    def test_historian_persists_and_exports_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            historian = Historian(str(Path(directory) / "history.sqlite3"))
            historian.add_sample({"time": "2026-10-12T12:00:00+00:00", "t1": 20, "t2": 21, "t3": 19, "setpoint": 30, "heater": False, "cooler": False, "mode": "SIMULATION", "quality": "GOOD"})
            historian.add_sample({"time": "2026-10-12T13:00:00+00:00", "t1": 25, "t2": 26, "t3": 24, "setpoint": 30, "heater": True, "cooler": False, "mode": "SIMULATION", "quality": "GOOD"})
            historian.add_setpoint("2026-10-12T12:00:00+00:00", 30)
            historian.add_event({"time": "2026-10-12T12:00:00+00:00", "severity": "WARNING", "source": "TEST", "message": "operator event"})
            self.assertEqual(len(historian.recent()), 2)
            self.assertEqual(len(historian.between("2026-10-12T12:30:00+00:00", "2026-10-12T13:30:00+00:00")), 1)
            self.assertEqual(historian.recent_events()[0]["message"], "operator event")
            self.assertEqual(historian.recent_setpoints()[0]["value"], 30)
            self.assertIn("T1, °C", historian.export_csv())


if __name__ == "__main__":
    unittest.main()