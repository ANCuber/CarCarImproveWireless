# hm10_bleak

Connects to an HM-10 straight from the PC's own Bluetooth adapter, using [bleak](https://github.com/hbldh/bleak). It exists for the direct BLE chat demo, [`chat_hm10-bleak.py`](../chat_hm10-bleak.py). To use the HM-10 from your own program, use [`hm10_esp32`](../hm10_esp32/README.md) instead.

```bash
pip install bleak
```

## Calls

bleak is asynchronous, so most calls need `await` inside an `async` function.

| Call | What it does |
|------|--------------|
| `HM10BleakClient(target_name=None, target_address=None)` | Chooses the HM-10 by name (`"HM10_Mega"`), by MAC address (`"88:C2:55:03:BC:D7"`), or both. |
| `await connect()` | Scans for up to 10 seconds, connects, and subscribes to notifications on characteristic `0xFFE1`. Returns `True` when connected, `False` if the device was not found. |
| `await send(message)` | Sends text in 20-byte chunks with a 20 ms pause between them. Does nothing if the link is down. |
| `listen()` | Not async. Returns the text received since the last call, `""` if none. |
| `await disconnect()` | Closes the BLE connection. |

`chat_hm10-bleak.py` shows them in use: one task prints what `listen()` returns every 0.1 s while another reads the keyboard and calls `send()`.
