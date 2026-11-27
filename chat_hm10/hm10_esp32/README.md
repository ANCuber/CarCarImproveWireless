# HM-10 ESP32 Bridge Interface

This Python module provides a high-level interface to interact with an ESP32 acting as a BLE-to-UART bridge. It allows a PC to communicate with an HM-10 Bluetooth module through an ESP32 by sending AT commands for configuration and raw data for GATT-based messaging.

---

## Features

* **Socket-style calls**: `connect()`, `write()`, `read()`, `readline()`, `close()`.
* **AT Command Parsing**: Handles specific commands implemented in the ESP32 firmware, including `AT+NAME`, `AT+STATUS`, and `AT+RESET`.
* **Log Filtering**: Automatically strips ESP32 system logs and ANSI color escape codes to extract clean Bluetooth communication data.
* **Automatic Concatenation**: Merges fragmented data packets (caused by the ESP32's 10-byte UART buffer) into single, readable strings.
* **NVS Support**: Interfaces with the ESP32's Non-Volatile Storage to query or update the target device name.

---

## Hardware Requirements

* **ESP32**: Running the [`esp32-hm10`](../../esp32-hm10/README.md) firmware.
* **HM-10 Bluetooth Module**: Configured to be discoverable by the ESP32.
* **Connection**: ESP32 connected to PC via USB/UART (e.g., `/dev/ttyUSB2`).

---

## Installation

Ensure you have `pyserial` installed:

```bash
pip install pyserial

```

---

## API Reference

The class works like a socket or a serial port: open it, `connect()`, then `write()` and `read()`.

```python
from hm10_esp32 import HM10ESP32Bridge

with HM10ESP32Bridge('/dev/ttyUSB0') as hm10:
    if hm10.connect('HM10_Mega', timeout=30):
        hm10.write("hello\n")
        print(hm10.readline(timeout=3))
```

### `HM10ESP32Bridge(port, rx_timeout=0.1)`

Opens the serial port at **115200** baud (fixed, matching the firmware). `rx_timeout` is the serial read timeout in seconds.

### `connect(name=None, timeout=-1, poll_interval=1.0)`

Waits until the ESP32 has a ready link to the HM-10, checking every `poll_interval` seconds.

* `name`: the HM-10 to connect to. If the ESP32 is set to another name, the name is changed and the ESP32 is restarted. `None` keeps the name stored in the ESP32.
* `timeout`: seconds to wait. `-1` waits until connected (stop with Ctrl+C).
* Returns: `True` once connected, `False` on timeout.
* Raises: `ConnectionError` if nothing on the port answers like an ESP32 with this firmware (wrong port, or firmware not flashed). It gives up after about 4 seconds.

### `is_connected()`

Returns `True` if the ESP32 currently has a ready link to the HM-10.

### `write(data)`

Sends `data` (`str`, encoded as UTF-8, or `bytes`) to the HM-10, which passes it to the Arduino's serial port.

* Returns: the number of bytes sent.
* The ESP32 drops data while the link is not ready. Call `connect()` first.
* The ESP32 forwards about 250 bytes per second. Its input buffer holds 2048 bytes, so a faster stream of long writes loses data.

### `read(size=-1, timeout=0)`

Returns text received from the HM-10.

* `size`: maximum number of characters to return. `-1` returns everything received so far.
* `timeout`: seconds to wait for the first character. `0` returns at once. `-1` waits until something arrives.
* Returns: a `str`, or `""` if nothing arrived in time.

### `readline(timeout=-1)`

Returns one line, including the `\n` at its end. Arduino's `println()` ends lines with `\r\n`, so call `.strip()` on the result.

* `timeout`: seconds to wait for the end of the line. `-1` waits until it arrives.
* Returns: the line, or `""` on timeout. A line that is still incomplete stays buffered for the next call.

### `close()`

Closes the serial port. **Call it before creating a new `HM10ESP32Bridge` on the same port.** Windows refuses to open a COM port that is still open, and reports "Access is denied". The class also works as a context manager: `with HM10ESP32Bridge(port) as bridge: ...` closes the port on exit.

### Lower-level calls

* `get_status()`: sends `AT+STATUS?`. Returns `"CONNECTED"`, `"DISCONNECTED"`, or `"TIMEOUT"` (no reply from the ESP32).
* `get_hm10_name()`: returns the target HM-10 name stored in the ESP32, or `None` if the ESP32 does not reply.
* `set_hm10_name(name)`: stores a new target name in the ESP32. Returns `True` if the ESP32 confirmed. Call `reset()` afterwards so the ESP32 connects to the new target.
* `reset(connect_timeout=15.0)`: restarts the ESP32, then waits up to `connect_timeout` seconds for the link. Returns `True` if the ESP32 confirmed the reset.
* `listen()`: same as `read()`. `send(text)`: same as `write(text)`. Both are the older names and still work.

### Limits

* **Text only.** Received bytes are decoded as UTF-8. Bytes that are not valid UTF-8 are dropped.
* **`OK+` prefix.** A BLE packet from the HM-10 that starts with `OK+` is taken as a reply from the ESP32 and does not reach `read()`.
* **AT commands in data.** The ESP32 handles `AT+RESET`, `AT+NAME?`, `AT+STATUS?` and `AT+NAME<name>` itself. Do not send these strings as data.
* **Threads.** One thread may call `read()` while another calls `write()`. The module keeps AT commands apart from data, so `is_connected()` right after `write()` is safe.

> **How data reaches Python:** the firmware prints every HM-10 notification and every `OK+...` reply as an ESP-IDF log line tagged `bt_com:` at INFO level. `listen()` and the AT helpers keep only records tagged `bt_com:`. A record ends at the log's colour-reset code (`ESC[0m`) followed by `\r\n`, so a payload may contain newlines and keeps its spaces. A record split across two serial reads is kept until its end arrives. If the firmware's log tag or log level changes, or log colours are turned off (`CONFIG_LOG_COLORS`), this module receives nothing.

---

## Example Usage

[`chat_hm10-esp32.py`](../chat_hm10-esp32.py) is a complete chat program: a background thread prints what `listen()` returns while the main thread sends what the user types.

---

## Firmware Notes

The bridge relies on the following logic within the ESP32 `main.c`:

* **UART Buffer**: The firmware reads 10 bytes at a time.
* **Logging**: Communication data is logged with the tag `bt_com`.
* **BLE Service**: The bridge targets Service `0xFFE0` and Characteristic `0xFFE1`.
