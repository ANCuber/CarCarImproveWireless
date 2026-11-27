import codecs
import re
import threading
import time

import serial


class HM10ESP32Bridge:
    """Talks to an HM-10 through an ESP32 flashed with the esp32-hm10 firmware.

    Typical use, similar to a socket:

        with HM10ESP32Bridge('COM3') as hm10:
            hm10.connect('HM10_Mega')
            hm10.write('hello\\n')
            line = hm10.readline(timeout=2)
    """

    # The firmware takes at most 10 bytes from its UART per cycle and recognises
    # an AT command only when a chunk holds nothing else. A command is therefore
    # sent only after the firmware has taken all earlier bytes, plus a gap, so
    # that it never shares a chunk with them.
    _CHUNK_SIZE = 10      # bytes the firmware takes per cycle
    _CHUNK_TIME = 0.04    # seconds per cycle (about 0.025 to 0.035 on the ESP32), with margin
    _COMMAND_GAP = 0.1    # seconds of silence before and after a command

    # Seconds between two looks at the serial port while waiting for data or a reply
    _POLL_INTERVAL = 0.001

    def __init__(self, port, rx_timeout=0.1):
        self.port = port
        self.ser = serial.Serial(port=port, baudrate=115200, timeout=rx_timeout)
        # ESP-IDF log record: ESC[0;32mI (<ms>) bt_com: <payload>ESC[0m\r\n
        # The payload may itself contain newlines, so the record ends at the
        # colour-reset code, not at the first line break.
        self.record_start = re.compile(rb'\x1b\[[0-9;]*m[A-Z] \(\d+\) bt_com: ')
        self.record_end = b'\x1b[0m\r\n'
        # Bytes read from the port that do not yet form a complete record
        self._rx_buf = b''
        # Text received from the HM-10 that read()/readline()/listen() has not returned yet
        self._data = ''
        # A multi-byte character may be split across two BLE packets
        self._decoder = codecs.getincrementaldecoder('utf-8')(errors='ignore')
        # OK+... replies to AT commands that no helper has consumed yet
        self._replies = []
        self._lock = threading.RLock()
        # Time at which the firmware has taken everything sent so far
        self._tx_drained_at = 0.0
        self._last_tx_was_command = False
        time.sleep(1)

    # ------------------------------------------------------------------
    # Serial plumbing
    # ------------------------------------------------------------------

    def _read_bt_com_payloads(self):
        """Returns the payloads (bytes) of all complete 'bt_com' records received so far."""
        if self.ser.in_waiting:
            self._rx_buf += self.ser.read_all()
        payloads = []
        while True:
            start = self.record_start.search(self._rx_buf)
            if not start:
                # Keep a record start that is still arriving; drop other output
                esc = self._rx_buf.rfind(b'\x1b')
                self._rx_buf = self._rx_buf[esc:] if esc >= 0 else b''
                break
            end = self._rx_buf.find(self.record_end, start.end())
            if end < 0:
                self._rx_buf = self._rx_buf[start.start():]
                break
            payload = self._rx_buf[start.end():end]
            # The ESP32 sends each '\n' in the payload as '\r\n'; undo that
            payloads.append(payload.replace(b'\r\n', b'\n'))
            self._rx_buf = self._rx_buf[end + len(self.record_end):]
        return payloads

    def _poll(self):
        """Reads the port and sorts what arrived into AT replies and HM-10 data."""
        with self._lock:
            for payload in self._read_bt_com_payloads():
                if payload.startswith(b'OK+'):
                    self._replies.append(payload.decode('utf-8', errors='ignore'))
                else:
                    self._data += self._decoder.decode(payload)

    def _transmit(self, raw, is_command):
        """Writes raw bytes, keeping AT commands apart from all other bytes."""
        with self._lock:
            if is_command or self._last_tx_was_command:
                wait = self._tx_drained_at + self._COMMAND_GAP - time.time()
                if wait > 0:
                    time.sleep(wait)
            self.ser.write(raw)
            self.ser.flush()
            chunks = -(-len(raw) // self._CHUNK_SIZE)
            self._tx_drained_at = max(time.time(), self._tx_drained_at) + chunks * self._CHUNK_TIME
            self._last_tx_was_command = is_command

    def _command(self, command, expected, timeout):
        """Sends an AT command and returns the first reply containing one of
        the strings in `expected`, or None on timeout."""
        with self._lock:
            self._poll()
            self._replies.clear()  # replies to earlier commands are stale
            self._transmit(command.encode('utf-8'), is_command=True)
            deadline = time.time() + timeout
            while True:
                self._poll()
                for reply in self._replies:
                    if any(e in reply for e in expected):
                        self._replies.clear()
                        return reply
                if time.time() >= deadline:
                    return None
                time.sleep(self._POLL_INTERVAL)

    # ------------------------------------------------------------------
    # ESP32 configuration (AT commands handled by the ESP32 itself)
    # ------------------------------------------------------------------

    def set_hm10_name(self, name, timeout=2.0):
        """
        Sends AT+NAME<name> and verifies OK+SET<name> reply.
        Returns True on success, False on timeout/failure.
        """
        return self._command(f"AT+NAME{name}", [f"OK+SET{name}"], timeout) is not None

    def get_hm10_name(self, timeout=2.0):
        """Queries the device name currently in NVS. Returns None on timeout."""
        reply = self._command("AT+NAME?", ["OK+NAME"], timeout)
        if reply is None:
            return None
        return reply.replace("OK+NAME", "").strip()

    def get_status(self, timeout=2.0):
        """Checks connection status via AT+STATUS?.
        Returns "CONNECTED", "DISCONNECTED" or "TIMEOUT"."""
        reply = self._command("AT+STATUS?", ["OK+CONN", "OK+UNCONN"], timeout)
        if reply is None:
            return "TIMEOUT"
        return "CONNECTED" if "OK+CONN" in reply else "DISCONNECTED"

    def reset(self, connect_timeout=15.0):
        """Triggers AT+RESET and returns True if OK+RESET is received.
        Then waits up to connect_timeout seconds until the ESP32 reports
        OK+CONN (link to the HM-10 ready for data)."""
        if self._command("AT+RESET", ["OK+RESET"], 10.0) is None:
            return False
        time.sleep(1)  # let the ESP32 reboot before querying it
        deadline = time.time() + connect_timeout
        while time.time() < deadline:
            if self.get_status(timeout=0.5) == "CONNECTED":
                break
        return True

    # ------------------------------------------------------------------
    # Socket-style API
    # ------------------------------------------------------------------

    @staticmethod
    def _deadline(timeout):
        """Time at which a wait of `timeout` seconds ends; None for no limit.
        A negative timeout (the documented value is -1) or None means no limit."""
        if timeout is None or timeout < 0:
            return None
        return time.time() + timeout

    def connect(self, name=None, timeout=-1, poll_interval=1.0):
        """Waits until the ESP32 has a ready link to the HM-10.

        name: HM-10 name to connect to. If the ESP32 is set to another name,
              it is changed and the ESP32 is restarted. None keeps the name
              stored in the ESP32.
        timeout: seconds to wait; -1 waits forever (stop with Ctrl+C).
        Returns True once connected, False on timeout.
        Raises ConnectionError if nothing on the port answers like an ESP32
        with the esp32-hm10 firmware (wrong port, or firmware not flashed).
        """
        deadline = self._deadline(timeout)
        if self.get_hm10_name() is None and self.get_hm10_name() is None:
            raise ConnectionError(
                f"No reply from the ESP32 on {self.port}. Check the port, "
                "and that the ESP32 is flashed with the esp32-hm10 firmware.")
        name_ok = name is None
        while True:
            if not name_ok:
                current = self.get_hm10_name()
                if current == name:
                    name_ok = True
                elif current is not None and self.set_hm10_name(name):
                    self.reset(connect_timeout=0)
                    name_ok = True
            if name_ok and self.get_status() == "CONNECTED":
                return True
            if deadline is not None and time.time() >= deadline:
                return False
            time.sleep(poll_interval)

    def is_connected(self):
        """True if the ESP32 currently has a ready link to the HM-10."""
        return self.get_status() == "CONNECTED"

    def write(self, data):
        """Sends text (str) or bytes to the HM-10. Returns the number of bytes sent.

        The ESP32 drops the data if the link is not ready, so call connect() first.
        """
        raw = data.encode('utf-8') if isinstance(data, str) else bytes(data)
        if raw:
            self._transmit(raw, is_command=False)
        return len(raw)

    def read(self, size=-1, timeout=0):
        """Returns text received from the HM-10.

        size: maximum number of characters to return; -1 returns everything received.
        timeout: seconds to wait for the first character. 0 returns at once,
                 -1 waits until something arrives.
        Returns "" if nothing arrived in time.
        """
        deadline = self._deadline(timeout)
        while True:
            with self._lock:
                self._poll()
                if self._data:
                    if size is None or size < 0:
                        size = len(self._data)
                    out, self._data = self._data[:size], self._data[size:]
                    return out
            if deadline is not None and time.time() >= deadline:
                return ""
            time.sleep(self._POLL_INTERVAL)

    def readline(self, timeout=-1):
        """Returns one line received from the HM-10, including its '\\n'.

        timeout: seconds to wait for the end of the line; -1 waits until it arrives.
        Returns "" on timeout. A line that is still incomplete stays buffered.
        """
        deadline = self._deadline(timeout)
        while True:
            with self._lock:
                self._poll()
                end = self._data.find('\n')
                if end >= 0:
                    out, self._data = self._data[:end + 1], self._data[end + 1:]
                    return out
            if deadline is not None and time.time() >= deadline:
                return ""
            time.sleep(self._POLL_INTERVAL)

    # ------------------------------------------------------------------
    # Older names, kept for existing scripts
    # ------------------------------------------------------------------

    def listen(self):
        """Returns everything received from the HM-10 since the last call ("" if nothing)."""
        return self.read()

    def send(self, text):
        """Sends data to be forwarded to HM-10 via GATT. Same as write()."""
        self.write(text)

    def close(self):
        """Explicitly close the serial port."""
        ser = getattr(self, 'ser', None)
        if ser and ser.is_open:
            ser.close()

    def __del__(self):
        """Ensure serial port is closed when object is destroyed."""
        self.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures port is closed."""
        self.close()
        return False
