#!/usr/bin/env python3
"""Заглушка прикладного сервиса для тестовой среды мониторинга.

Только стандартная библиотека: образ собирается за секунды и не тянет
колёса с PyPI, а нам от сервиса нужны ровно три вещи - отвечать на
/health, отдавать /metrics и уметь по команде притвориться сломанным.

Состояния (управляются через /chaos/*):
  ok       - /health 200, app_degraded 0  -> зелёный
  degraded - /health 200, app_degraded 1  -> жёлтый
  down     - /health 503                  -> красный
"""
import json
import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SERVICE = os.environ.get("SERVICE_NAME", "python-service")
GROUP = os.environ.get("SERVICE_GROUP", "apps")
ROLE = os.environ.get("SERVICE_ROLE", "generic")
PORT = int(os.environ.get("PORT", "8000"))
# Стартовая задержка: у части сервисов прогрев дольше, дашборд должен
# показывать это как временный красный, а не мигать.
WARMUP = float(os.environ.get("WARMUP_SECONDS", "3"))

_started = time.monotonic()
_lock = threading.Lock()
_state = {
    "mode": "ok",
    "requests": 0,
    "errors": 0,
    "jobs": 0,
}


def _ready() -> bool:
    return time.monotonic() - _started >= WARMUP


def _worker() -> None:
    """Фоновая активность, чтобы счётчики не стояли на нуле и rate() что-то показывал."""
    while True:
        time.sleep(random.uniform(0.5, 2.0))
        with _lock:
            mode = _state["mode"]
            _state["jobs"] += random.randint(1, 4)
            if mode == "degraded":
                _state["errors"] += random.randint(1, 3)


def _metrics() -> str:
    with _lock:
        mode = _state["mode"]
        requests_ = _state["requests"]
        errors = _state["errors"]
        jobs = _state["jobs"]

    labels = f'service="{SERVICE}",group="{GROUP}",role="{ROLE}"'
    up = 0 if mode == "down" or not _ready() else 1
    degraded = 1 if mode == "degraded" else 0
    lines = [
        "# HELP app_up Сервис считает себя работоспособным.",
        "# TYPE app_up gauge",
        f"app_up{{{labels}}} {up}",
        "# HELP app_degraded Сервис жив, но работает деградированно.",
        "# TYPE app_degraded gauge",
        f"app_degraded{{{labels}}} {degraded}",
        "# HELP app_uptime_seconds Время с момента старта процесса.",
        "# TYPE app_uptime_seconds gauge",
        f"app_uptime_seconds{{{labels}}} {time.monotonic() - _started:.1f}",
        "# HELP app_requests_total Обработано HTTP-запросов.",
        "# TYPE app_requests_total counter",
        f"app_requests_total{{{labels}}} {requests_}",
        "# HELP app_errors_total Ошибок обработки.",
        "# TYPE app_errors_total counter",
        f"app_errors_total{{{labels}}} {errors}",
        "# HELP app_jobs_total Обработано фоновых задач (для воркеров Zeebe).",
        "# TYPE app_jobs_total counter",
        f"app_jobs_total{{{labels}}} {jobs}",
    ]
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: A003 - глушим шум в stdout
        pass

    def _send(self, code: int, body: str, ctype: str = "text/plain; charset=utf-8") -> None:
        raw = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        with _lock:
            _state["requests"] += 1
            mode = _state["mode"]

        path = self.path.split("?", 1)[0].rstrip("/") or "/"

        if path == "/metrics":
            self._send(200, _metrics())
        elif path in ("/health", "/healthz", "/health/ready"):
            if mode == "down":
                self._send(503, json.dumps({"status": "down", "service": SERVICE}))
            elif not _ready():
                self._send(503, json.dumps({"status": "starting", "service": SERVICE}))
            else:
                status = "degraded" if mode == "degraded" else "ok"
                self._send(200, json.dumps({"status": status, "service": SERVICE}))
        elif path.startswith("/chaos/"):
            want = path.rsplit("/", 1)[-1]
            if want not in ("ok", "degrade", "down"):
                self._send(400, json.dumps({"error": "ok|degrade|down"}))
                return
            with _lock:
                _state["mode"] = "degraded" if want == "degrade" else want
            self._send(200, json.dumps({"service": SERVICE, "mode": _state["mode"]}))
        elif path == "/":
            self._send(200, json.dumps({"service": SERVICE, "group": GROUP, "role": ROLE, "mode": mode}))
        else:
            self._send(404, json.dumps({"error": "not found"}))


if __name__ == "__main__":
    threading.Thread(target=_worker, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
