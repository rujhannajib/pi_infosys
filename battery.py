import smbus2
import time
import threading

# ── Configuration ──────────────────────────────────────────────────────────────
BATTERY_MIN_V = 6.0    # Voltage at 0%
BATTERY_MAX_V = 8.4    # Voltage at 100%
POLL_INTERVAL = 30     # Seconds between sensor readings

# ── INA219 Driver ──────────────────────────────────────────────────────────────
_REG_CONFIG      = 0x00
_REG_BUSVOLTAGE  = 0x02
_REG_POWER       = 0x03
_REG_CURRENT     = 0x04
_REG_CALIBRATION = 0x05


class INA219:
    def __init__(self, i2c_bus=1, addr=0x42):
        self.bus = smbus2.SMBus(i2c_bus)
        self.addr = addr
        self._cal_value = 4096
        self._current_lsb = 0.1
        self._power_lsb = 0.002
        self._configure()

    def _configure(self):
        self.write(_REG_CALIBRATION, self._cal_value)
        config = (0x01 << 13) | (0x03 << 11) | (0x0D << 7) | (0x0D << 3) | 0x07
        self.write(_REG_CONFIG, config)

    def read(self, address):
        data = self.bus.read_i2c_block_data(self.addr, address, 2)
        return (data[0] * 256) + data[1]

    def write(self, address, data):
        self.bus.write_i2c_block_data(
            self.addr, address, [(data & 0xFF00) >> 8, data & 0xFF]
        )

    def getBusVoltage_V(self):
        self.write(_REG_CALIBRATION, self._cal_value)
        return (self.read(_REG_BUSVOLTAGE) >> 3) * 0.004

    def getCurrent_mA(self):
        value = self.read(_REG_CURRENT)
        if value > 32767:
            value -= 65535
        return value * self._current_lsb

    def getPower_W(self):
        self.write(_REG_CALIBRATION, self._cal_value)
        value = self.read(_REG_POWER)
        if value > 32767:
            value -= 65535
        return value * self._power_lsb


# ── Shared cache ───────────────────────────────────────────────────────────────
_battery_cache = {
    "voltage": None,
    "current_a": None,
    "power_w": None,
    "percent": None,
    "timestamp": 0,
    "error": None,
}
_cache_lock = threading.Lock()


def _calc_percent(voltage):
    p = (voltage - BATTERY_MIN_V) / (BATTERY_MAX_V - BATTERY_MIN_V) * 100
    return max(0.0, min(100.0, p))


def _poll_loop():
    ina219 = None

    while True:
        try:
            if ina219 is None:
                ina219 = INA219(addr=0x42)

            voltage = ina219.getBusVoltage_V()
            current = ina219.getCurrent_mA()
            power = ina219.getPower_W()

            with _cache_lock:
                _battery_cache["voltage"] = round(voltage, 3)
                _battery_cache["current_a"] = round(current / 1000, 4)
                _battery_cache["power_w"] = round(power, 3)
                _battery_cache["percent"] = round(_calc_percent(voltage), 1)
                _battery_cache["timestamp"] = int(time.time())
                _battery_cache["error"] = None

        except Exception as e:
            ina219 = None  # force re-init on next cycle (handles I2C hiccups)
            with _cache_lock:
                _battery_cache["error"] = str(e)

        time.sleep(POLL_INTERVAL)


def start_battery_monitor():
    """Launch the background polling thread. Call once at app startup."""
    threading.Thread(target=_poll_loop, daemon=True).start()


def get_battery_data():
    """Return a snapshot of the latest battery reading."""
    with _cache_lock:
        data = dict(_battery_cache)
    data["stale"] = (time.time() - data["timestamp"]) > POLL_INTERVAL * 3
    return data