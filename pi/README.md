# Raspberry Pi Deployment Guide

Hardware: Raspberry Pi 4 × N, Waveshare **SX1262 868M LoRa HAT** (SKU 16806) on each.

```
  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
  │ Sensor Pi 1  │   │ Sensor Pi 2  │   │ Sensor Pi N  │
  │ addr = 1     │   │ addr = 2     │   │ addr = N     │
  │ DHT22 + MQ-2 │   │ DHT22 + MQ-2 │   │ DHT22 + MQ-2 │
  └──────┬───────┘   └──────┬───────┘   └──────┬───────┘
         │                  │                  │
         └────── LoRa 866 MHz, 12-byte packets ┘
                            │
                   ┌────────▼────────┐
                   │  Gateway Pi     │  addr = 0, has internet
                   │  gateway.py     │  → SQLite
                   │  api.py         │  → serves dashboard :5000
                   └─────────────────┘
```

## Two things to know before you start

**1. Your HAT is not LoRaWAN.** Waveshare states this explicitly: the SX1262 HAT
uses a private point-to-point protocol. TTN, ChirpStack, and every LoRaWAN
tutorial you'll find do not apply. The nodes address each other directly by
number, which is simpler and fine for a closed network like this one.

**2. The Pi has no analog input.** The MQ-2's AO pin outputs a voltage the Pi
physically cannot read. You have three options, and `SMOKE_MODE` in
`node/sensors.py` switches between them:

| Mode | Extra hardware | Smoke data quality |
|---|---|---|
| `simulate` | none | fake values — develop the software today |
| `digital` | none | on/off only, from the MQ-2's DO pin |
| `adc` | MCP3008 (~₹150) | real ppm curve — what you want for the report |

Start on `simulate`, prove the radio link works, then add the MCP3008.

## Frequency: use 866, not 868

The box says 868M, but that's the hardware band (850–930 MHz tunable), not a
fixed channel. India's licence-exempt allocation is **865–867 MHz**; 868 MHz is
the European band. `config.py` defaults to `FREQ = 866` for this reason. Every
Pi must use the same value or they won't hear each other.

---

## Step 1 — HAT jumpers (all Pis)

- **UART selection jumper → position B** (LoRa module talks to the Pi)
- **Remove the M0 and M1 jumper caps.** The driver drives these from BCM22 and
  BCM27; leaving the caps on means the Pi can't switch the module between
  config and transmit mode.
- Screw on the SMA antenna **before powering up**. Transmitting without an
  antenna can damage the PA.

## Step 2 — Enable the serial port (all Pis)

```bash
sudo raspi-config
#   Interface Options → Serial Port
#   "login shell over serial?"  → No
#   "serial port hardware enabled?" → Yes
sudo reboot
```

Confirm which device you actually got:

```bash
ls -l /dev/serial*
```

If `serial0` points at something other than `ttyS0`, set it:
`export LORA_SERIAL=/dev/ttyAMA0`

## Step 3 — Waveshare driver (all Pis)

```bash
cd ~
wget https://files.waveshare.com/upload/1/18/SX126X_LoRa_HAT_CODE.zip
unzip SX126X_LoRa_HAT_CODE.zip
```

Copy `sx126x.py` next to the script that needs it:

```bash
cp SX126X_LoRa_HAT_Code/raspberrypi/python/sx126x.py ~/forest-fire-dashboard/pi/node/      # sensor Pis
cp SX126X_LoRa_HAT_Code/raspberrypi/python/sx126x.py ~/forest-fire-dashboard/pi/gateway/   # gateway Pi
```

It is not bundled here because it's Waveshare's code — pull it from source so
you get any fixes they publish.

## Step 4 — Sensor wiring (sensor Pis only)

**DHT22** (temperature + humidity)

| DHT22 | Pi |
|---|---|
| VCC | 3.3 V (pin 1) |
| DATA | BCM 4 (pin 7) — 10 kΩ pull-up to 3.3 V |
| GND | GND (pin 9) |

**MQ-2 in `digital` mode**

| MQ-2 | Pi |
|---|---|
| VCC | 5 V (pin 2) |
| DO | BCM 17 (pin 11) |
| GND | GND |

Set the trip point with the pot on the MQ-2 board.

**MQ-2 in `adc` mode — MCP3008 on SPI**

| MCP3008 | Pi |
|---|---|
| VDD, VREF | 3.3 V |
| AGND, DGND | GND |
| CLK | BCM 11 / SCLK (pin 23) |
| DOUT | BCM 9 / MISO (pin 21) |
| DIN | BCM 10 / MOSI (pin 19) |
| CS | BCM 8 / CE0 (pin 24) |
| CH0 | MQ-2 AO |
| CH1 | battery divider midpoint (optional) |

Enable SPI: `sudo raspi-config` → Interface Options → SPI → Yes.

> The MQ-2 runs on 5 V and its AO can swing above 3.3 V, which would damage the
> MCP3008. Put a divider (two 10 kΩ resistors) between AO and CH0, or use a
> 3.3 V-tolerant MQ-2 breakout.

The LoRa HAT uses UART (BCM 14/15) plus BCM 22/27 — no conflict with SPI or
BCM 4/17, so everything coexists. You'll want a stacking header to reach the
GPIO pins under the HAT.

## Step 5 — Python packages

Gateway Pi:

```bash
pip3 install --break-system-packages flask flask-cors pyserial RPi.GPIO
```

Sensor Pis:

```bash
pip3 install --break-system-packages pyserial RPi.GPIO
# only if using real sensors:
sudo apt install -y libgpiod2
pip3 install --break-system-packages adafruit-circuitpython-dht adafruit-circuitpython-mcp3xxx
```

## Step 6 — Configure node identities

Edit `pi/common/config.py` on **every** Pi so the `NODES` dictionary matches
your real deployment — names and GPS coordinates. The gateway serves these to
the dashboard, so wrong coordinates mean pins in the wrong place on the map.

Also change `CRYPT_KEY` from the default. Without that, anyone with the same
HAT on 866 MHz can inject readings into your dashboard.

## Step 7 — Bring it up

**Gateway Pi**, two terminals:

```bash
cd ~/forest-fire-dashboard/pi/gateway
python3 gateway.py          # LoRa receiver → database
```

```bash
cd ~/forest-fire-dashboard/pi/gateway
python3 api.py              # HTTP API + dashboard on :5000
```

**Each sensor Pi:**

```bash
cd ~/forest-fire-dashboard/pi/node
NODE_ID=1 SMOKE_MODE=simulate DHT_MODE=simulate python3 sensor_node.py
```

Change `NODE_ID` per Pi. Once sensors are wired: `SMOKE_MODE=adc DHT_MODE=dht22`.

**Switch the dashboard to live data** — one line in `index.html`:

```html
<!-- <script src="data-source.js"></script> -->
<script src="data-source.live.js"></script>
```

Open `http://<gateway-pi-ip>:5000` from any device on the network.

## Step 8 — Run on boot

`/etc/systemd/system/fire-gateway.service` on the gateway:

```ini
[Unit]
Description=Forest fire LoRa gateway
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/forest-fire-dashboard/pi/gateway
ExecStart=/usr/bin/python3 gateway.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Same pattern for `fire-api.service` (`ExecStart=/usr/bin/python3 api.py`) and,
on each sensor Pi, `fire-node.service` with `Environment="NODE_ID=1"`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now fire-gateway fire-api
```

---

## Testing without leaving your desk

1. Run everything in `simulate` mode. If packets arrive, the radio link and
   database are proven and any later problem is a sensor problem.
2. `curl http://localhost:5000/api/health` — should show `totalReadings` climbing.
3. `curl http://localhost:5000/api/nodes` — should list every node with fresh timestamps.
4. To exercise the fire alert without an actual fire, temporarily lower the
   thresholds in `config.py` (e.g. `temp_high: 28`) and restart a node.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `setting fail, setting again` on startup | Jumper not on B, or M0/M1 caps still fitted |
| No packets received | Mismatched `FREQ`, `AIR_SPEED`, `NET_ID` or `CRYPT_KEY` between Pis |
| Serial permission denied | `sudo usermod -aG dialout $USER`, then log out and back in |
| DHT22 returns None frequently | Normal for the part — the code retries and reuses the last value |
| Short range | Antenna near metal or at ground level; raise it and keep air speed at 2400 |
| Dashboard shows all nodes offline | `gateway.py` not running, or `OFFLINE_AFTER` shorter than your uplink interval |

## What's still simulated

Nothing on the transport side — the protocol, gateway, database and API are
real. Sensor values are simulated only while `SMOKE_MODE`/`DHT_MODE` say so.
The `Simulate fire event` button in the dashboard is disabled on live data;
`data-source.live.js` logs a note explaining why when you click it.
