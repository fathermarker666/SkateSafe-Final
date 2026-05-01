import json
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

import serial


def _parse_serial_sample(line):
    clean_line = line.strip()
    if not clean_line:
        return None, "空白資料列"

    if any(marker in clean_line for marker in ("SkateSafe", "模式啟動", "✅")):
        return None, "略過啟動訊息"

    fields = [field.strip() for field in clean_line.split(",")]
    if not fields or not fields[0]:
        return None, "缺少總加速度欄位"

    try:
        total_value = float(fields[0])
    except ValueError:
        return None, f"總加速度不是數字: {clean_line[:60]}"

    axis_values = []
    for axis_index in range(1, 4):
        if axis_index >= len(fields) or not fields[axis_index]:
            axis_values.append(None)
            continue
        try:
            axis_values.append(float(fields[axis_index]))
        except ValueError:
            return None, f"軸向資料不是數字: {fields[axis_index][:20]}"

    return {
        "raw_line": clean_line,
        "total_g": total_value,
        "x_g": axis_values[0],
        "y_g": axis_values[1],
        "z_g": axis_values[2],
    }, ""


def _make_handler(runtime):
    class MonitorRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?", 1)[0] != "/snapshot":
                self.send_response(404)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                return

            payload = json.dumps(runtime.get_snapshot(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    return MonitorRequestHandler


class SerialMonitorRuntime:
    def __init__(
        self,
        *,
        port,
        patient_id,
        user_id,
        impact_threshold,
        on_impact: Callable[[str, str, float, list[float]], str],
        history_size=40,
    ):
        self.port = port
        self.patient_id = patient_id
        self.user_id = user_id
        self.impact_threshold = float(impact_threshold)
        self.history_size = history_size
        self.on_impact = on_impact

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._http_server = None
        self._http_thread = None
        self._serial = None

        self._history = deque([0.0] * history_size, maxlen=history_size)
        self._raw_total_window = deque(maxlen=12)
        self._smooth_val = 0.0
        self._offset = 0.0
        self._latest_value = 0.0
        self._latest_raw_total = None
        self._latest_raw_line = ""
        self._last_error = ""
        self._unit_mode = "未判定"
        self._status = "idle"
        self._updated_at = datetime.now().isoformat()
        self._has_impact_occurred = False
        self._latest_impact_g = None
        self._latest_impact_fhir_json = ""

    def start(self):
        if self._thread and self._thread.is_alive():
            return

        self._start_http_server()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._close_serial()
        self._stop_http_server()

    @property
    def server_url(self):
        if not self._http_server:
            return ""
        return f"http://127.0.0.1:{self._http_server.server_address[1]}"

    def update_config(self, *, impact_threshold=None):
        with self._lock:
            if impact_threshold is not None:
                self.impact_threshold = float(impact_threshold)

    def calibrate_zero(self):
        with self._lock:
            recent_window = list(self._history)[-10:]
            if recent_window:
                self._offset = sum(recent_window) / len(recent_window)

    def get_snapshot(self):
        with self._lock:
            return {
                "monitoring": not self._stop_event.is_set() and self._status not in ("stopped", "error"),
                "status": self._status,
                "latest_value": self._latest_value,
                "latest_raw_total": self._latest_raw_total,
                "history": list(self._history),
                "impact_threshold": self.impact_threshold,
                "has_impact_occurred": self._has_impact_occurred,
                "latest_impact_g": self._latest_impact_g,
                "latest_impact_fhir_json": self._latest_impact_fhir_json,
                "debug": {
                    "raw_line": self._latest_raw_line,
                    "last_error": self._last_error,
                    "unit_mode": self._unit_mode,
                },
                "updated_at": self._updated_at,
            }

    def _start_http_server(self):
        if self._http_server:
            return

        self._http_server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self))
        self._http_thread = threading.Thread(
            target=self._http_server.serve_forever,
            kwargs={"poll_interval": 0.1},
            daemon=True,
        )
        self._http_thread.start()

    def _stop_http_server(self):
        if not self._http_server:
            return

        self._http_server.shutdown()
        self._http_server.server_close()
        if self._http_thread and self._http_thread.is_alive():
            self._http_thread.join(timeout=2)
        self._http_server = None
        self._http_thread = None

    def _open_serial(self):
        try:
            self._serial = serial.Serial(self.port, 115200, timeout=0.05)
        except serial.SerialException:
            self._serial = None
            with self._lock:
                self._status = "error"
                self._last_error = "序列埠無法開啟"
                self._updated_at = datetime.now().isoformat()
            return False

        with self._lock:
            self._status = "running"
            self._last_error = ""
            self._updated_at = datetime.now().isoformat()
        return True

    def _close_serial(self):
        if self._serial is None:
            return
        try:
            if self._serial.is_open:
                self._serial.close()
        except serial.SerialException:
            pass
        finally:
            self._serial = None

    def _normalize_total_g(self, raw_total):
        self._raw_total_window.append(float(raw_total))
        near_gravity_count = sum(7.0 <= value <= 12.0 for value in self._raw_total_window)
        if len(self._raw_total_window) >= 6 and near_gravity_count >= max(4, len(self._raw_total_window) - 2):
            self._unit_mode = "m/s² -> G"
            return raw_total / 9.80665

        self._unit_mode = "原始值視為 G"
        return raw_total

    def _handle_impact(self, g_force):
        history_snapshot = list(self._history)
        fhir_json = self.on_impact(self.user_id, self.patient_id, g_force, history_snapshot)
        with self._lock:
            self._has_impact_occurred = True
            self._latest_impact_g = g_force
            self._latest_impact_fhir_json = fhir_json
            self._updated_at = datetime.now().isoformat()

    def _run_loop(self):
        if not self._open_serial():
            return

        while not self._stop_event.is_set():
            processed = 0
            try:
                waiting = self._serial.in_waiting if self._serial else 0
            except serial.SerialException:
                with self._lock:
                    self._status = "error"
                    self._last_error = "序列埠讀取失敗"
                    self._updated_at = datetime.now().isoformat()
                break

            while waiting > 0 and processed < 12 and not self._stop_event.is_set():
                try:
                    line = self._serial.readline().decode("utf-8", errors="ignore").strip()
                except serial.SerialException:
                    with self._lock:
                        self._status = "error"
                        self._last_error = "序列埠讀取失敗"
                        self._updated_at = datetime.now().isoformat()
                    self._stop_event.set()
                    break

                with self._lock:
                    self._latest_raw_line = line

                if not line:
                    processed += 1
                    try:
                        waiting = self._serial.in_waiting if self._serial else 0
                    except serial.SerialException:
                        waiting = 0
                    continue

                parsed_sample, parse_error = _parse_serial_sample(line)
                if parse_error:
                    with self._lock:
                        self._last_error = parse_error
                        self._updated_at = datetime.now().isoformat()
                    processed += 1
                    try:
                        waiting = self._serial.in_waiting if self._serial else 0
                    except serial.SerialException:
                        waiting = 0
                    continue

                raw_val = parsed_sample["total_g"]
                if abs(raw_val) > 150.0:
                    with self._lock:
                        self._last_error = f"略過異常峰值: {raw_val:.2f}"
                        self._updated_at = datetime.now().isoformat()
                    processed += 1
                    try:
                        waiting = self._serial.in_waiting if self._serial else 0
                    except serial.SerialException:
                        waiting = 0
                    continue

                normalized_val = self._normalize_total_g(raw_val)
                self._smooth_val = (normalized_val * 0.15) + (self._smooth_val * 0.85)
                value = self._smooth_val - self._offset
                if abs(value) < 0.15:
                    value = 0.0

                with self._lock:
                    self._latest_raw_total = normalized_val
                    self._latest_value = value
                    self._last_error = ""
                    self._history.append(value)
                    self._updated_at = datetime.now().isoformat()
                    impact_threshold = self.impact_threshold

                if value >= impact_threshold:
                    self._handle_impact(value)

                processed += 1
                try:
                    waiting = self._serial.in_waiting if self._serial else 0
                except serial.SerialException:
                    waiting = 0

            time.sleep(0.02)

        self._close_serial()
        with self._lock:
            if self._status != "error":
                self._status = "stopped"
            self._updated_at = datetime.now().isoformat()
