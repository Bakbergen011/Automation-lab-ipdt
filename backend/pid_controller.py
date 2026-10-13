class PIDController:
    def __init__(self, kp: float = 5.0, ki: float = 0.08, kd: float = 0.0):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.integral = 0.0
        self.previous_error: float | None = None

    def reset(self) -> None:
        self.integral = 0.0
        self.previous_error = None

    def update(self, setpoint: float, measured: float, dt: float) -> float:
        error = setpoint - measured
        dt = max(0.001, min(float(dt), 10.0))
        self.integral = max(-100.0, min(100.0, self.integral + error * dt))
        derivative = 0.0 if self.previous_error is None else (error - self.previous_error) / dt
        self.previous_error = error
        return max(0.0, min(100.0, self.kp * error + self.ki * self.integral + self.kd * derivative))


def interlock(heater: bool, cooler: bool) -> tuple[bool, bool]:
    if heater and cooler:
        raise ValueError("Защита: нагрев и охлаждение не могут быть включены одновременно")
    return heater, cooler