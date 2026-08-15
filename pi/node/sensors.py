"""
sensors.py — sensor reading for a forest-fire node Pi.

THE ONE HARDWARE GOTCHA: the Raspberry Pi has no analog input.

The MQ-2 smoke sensor outputs an analog voltage on AO. The Pi cannot read
that pin directly — you need an ADC chip in between. This module supports
three modes so you can start testing today and add hardware as it arrives:

  SMOKE_MODE=adc      MCP3008 on SPI. Real ppm estimate. What you want.
  SMOKE_MODE=digital  MQ-2 DO pin only. Gives 0 or 400 ppm, nothing between.
                      Works with zero extra parts, but the dashboard's smoke
                      trend line becomes a square wave. Fine for a demo.
  SMOKE_MODE=simulate No hardware at all. Plausible values for development.

Battery percentage has the same problem — measuring pack voltage needs the
ADC too. Without it, battery reports a fixed 100.

Install (on each sensor Pi):
    sudo apt install python3-pip
    pip3 install adafruit-circuitpython-dht adafruit-circuitpython-mcp3xxx
    sudo apt install libgpiod2
"""

import os
import random
import time

SMOKE_MODE = os.environ.get('SMOKE_MODE', 'simulate').lower()
DHT_MODE = os.environ.get('DHT_MODE', 'simulate').lower()   # 'dht22' or 'simulate'

DHT_GPIO = int(os.environ.get('DHT_GPIO', 4))               # BCM 4 = physical pin 7
MQ2_DIGITAL_GPIO = int(os.environ.get('MQ2_DIGITAL_GPIO', 17))
MQ2_ADC_CHANNEL = int(os.environ.get('MQ2_ADC_CHANNEL', 0))
BATTERY_ADC_CHANNEL = int(os.environ.get('BATTERY_ADC_CHANNEL', 1))

# Battery divider: two resistors scaling pack voltage into the ADC's 0–3.3 V.
# With R1=100k (to +V) and R2=100k (to GND) the ratio is 2.0.
BATTERY_DIVIDER = float(os.environ.get('BATTERY_DIVIDER', 2.0))
BATTERY_FULL_V = float(os.environ.get('BATTERY_FULL_V', 4.2))    # 1S LiPo
BATTERY_EMPTY_V = float(os.environ.get('BATTERY_EMPTY_V', 3.2))

_dht = None
_mcp = None
_gpio_ready = False


# ---------------------------------------------------------------- setup ----

def _init_dht():
    global _dht
    if _dht is not None:
        return _dht
    import board
    import adafruit_dht
    pin = getattr(board, 'D%d' % DHT_GPIO)
    # use_pulseio=False is required on Pi 4 running Bookworm
    _dht = adafruit_dht.DHT22(pin, use_pulseio=False)
    return _dht


def _init_mcp():
    global _mcp
    if _mcp is not None:
        return _mcp
    import board
    import busio
    import digitalio
    import adafruit_mcp3xxx.mcp3008 as MCP
    spi = busio.SPI(clock=board.SCK, MISO=board.MISO, MOSI=board.MOSI)
    cs = digitalio.DigitalInOut(board.D8)     # CE0
    _mcp = MCP.MCP3008(spi, cs)
    return _mcp


def _init_gpio():
    global _gpio_ready
    if _gpio_ready:
        return
    import RPi.GPIO as GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(MQ2_DIGITAL_GPIO, GPIO.IN)
    _gpio_ready = True


# ------------------------------------------------------------- readings ----

def read_temp_humidity():
    """Return (temp_c, humidity_pct, ok). DHT22 fails a read fairly often —
    that is normal for the part, so a failure returns ok=False rather than
    raising, and the node keeps its previous value."""
    if DHT_MODE == 'simulate':
        hour = time.localtime().tm_hour
        base = 24 + 6 * (1 if 11 <= hour <= 17 else -1) * random.uniform(0.4, 1.0)
        return round(base + random.uniform(-1, 1), 1), \
               round(58 - (base - 24) * 1.8 + random.uniform(-3, 3), 1), True

    for attempt in range(3):
        try:
            sensor = _init_dht()
            temp = sensor.temperature
            hum = sensor.humidity
            if temp is not None and hum is not None:
                return round(float(temp), 1), round(float(hum), 1), True
        except RuntimeError:
            # DHT22 timing miss — retry is the documented remedy
            time.sleep(2)
        except Exception:
            break
    return None, None, False


def read_smoke():
    """Return (ppm_estimate, ok)."""
    if SMOKE_MODE == 'simulate':
        return round(45 + random.uniform(-12, 25)), True

    if SMOKE_MODE == 'digital':
        try:
            _init_gpio()
            import RPi.GPIO as GPIO
            # MQ-2 DO is active-LOW when gas exceeds the pot-set threshold
            tripped = GPIO.input(MQ2_DIGITAL_GPIO) == 0
            return (400 if tripped else 60), True
        except Exception:
            return None, False

    try:
        from adafruit_mcp3xxx.analog_in import AnalogIn
        import adafruit_mcp3xxx.mcp3008 as MCP
        mcp = _init_mcp()
        pin = getattr(MCP, 'P%d' % MQ2_ADC_CHANNEL)
        chan = AnalogIn(mcp, pin)
        return round(_mq2_ppm(chan.voltage)), True
    except Exception:
        return None, False


def _mq2_ppm(voltage, rl=5.0, r0=9.83):
    """Rough MQ-2 ppm estimate from the sensor's datasheet curve.

    This is genuinely approximate. R0 is the sensor resistance in clean air
    and varies part to part — calibrate yours by running the sensor for
    24–48 h in clean air and recording the steady voltage, then solve for R0.
    Until you do, treat ppm as a relative trend, not an absolute measurement.
    """
    if voltage <= 0.01:
        return 0
    rs = (3.3 - voltage) / voltage * rl
    ratio = rs / r0
    # log-log fit to the smoke curve: ppm = a * ratio^b
    ppm = 1000.0 * (ratio ** -2.3)
    return max(0, min(10000, ppm))


def read_battery():
    """Return (percent, ok). Needs the MCP3008; otherwise reports 100."""
    if SMOKE_MODE != 'adc':
        return 100.0, True
    try:
        from adafruit_mcp3xxx.analog_in import AnalogIn
        import adafruit_mcp3xxx.mcp3008 as MCP
        mcp = _init_mcp()
        pin = getattr(MCP, 'P%d' % BATTERY_ADC_CHANNEL)
        volts = AnalogIn(mcp, pin).voltage * BATTERY_DIVIDER
        pct = (volts - BATTERY_EMPTY_V) / (BATTERY_FULL_V - BATTERY_EMPTY_V) * 100.0
        return round(max(0.0, min(100.0, pct)), 1), True
    except Exception:
        return None, False


def read_all(previous=None):
    """Read every sensor once.

    Returns (reading_dict, any_error). On a failed sensor the previous value
    is reused so one bad DHT22 cycle doesn't publish a fake 0 °C that would
    skew the 7-day chart.
    """
    previous = previous or {'temp': 25.0, 'hum': 50.0, 'smoke': 50.0, 'batt': 100.0}
    error = False

    temp, hum, ok = read_temp_humidity()
    if not ok or temp is None:
        temp, hum, error = previous['temp'], previous['hum'], True

    smoke, ok = read_smoke()
    if not ok or smoke is None:
        smoke, error = previous['smoke'], True

    batt, ok = read_battery()
    if not ok or batt is None:
        batt, error = previous['batt'], True

    return {
        'temp': float(temp),
        'hum': float(hum),
        'smoke': float(smoke),
        'batt': float(batt),
    }, error


def is_simulated():
    return SMOKE_MODE == 'simulate' and DHT_MODE == 'simulate'
