#!/usr/bin/env python3
"""Проверка дашборда без входа в Grafana.

Grafana лишь рисует то, что вернёт Prometheus, поэтому осмысленная проверка -
прогнать запросы всех панелей и убедиться, что каждый отдаёт серии. Пустой
результат означает пустую панель, даже если сам дашборд загрузился.
"""
import json
import pathlib
import subprocess
import urllib.parse

PROM = "http://127.0.0.1:9090/api/v1/query"
# Файл генерируется Ansible, имя берётся из mon_dashboard_uid.
DASH = sorted(pathlib.Path("/home/marov/monitoring-lab/grafana/dashboards").glob("*.json"))[0]


def query(expr: str) -> int:
    url = PROM + "?query=" + urllib.parse.quote(expr)
    out = subprocess.run(["curl", "-sf", url], capture_output=True, text=True).stdout
    if not out:
        return -1
    return len(json.loads(out)["data"]["result"])


if __name__ == "__main__":
    dash = json.loads(DASH.read_text(encoding="utf-8"))
    failed = 0
    for panel in dash["panels"]:
        expr = panel["targets"][0]["expr"]
        n = query(expr)
        if n <= 0:
            failed += 1
        mark = "OK   " if n > 0 else "ПУСТО"
        print(f"  {mark} {panel['title']:30} {expr:32} -> {n} серий")
    print()
    print(f"  панелей: {len(dash['panels'])}, пустых: {failed}")
    raise SystemExit(1 if failed else 0)
