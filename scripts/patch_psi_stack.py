#!/usr/bin/env python3
"""Минимальная правка существующего psi-stack под лабораторию.

Три изменения, все обратимые (рядом лежат .bak-* от install.sh):
  1. Prometheus начинает принимать remote_write - через него пишет Alloy;
  2. правила и дашборды лаборатории подключаются монтированием, а не
     копированием, чтобы источник правды остался в monitoring-lab;
  3. Prometheus узнаёт про Alertmanager.

Скрипт идемпотентен: повторный запуск ничего не дублирует.
"""
import pathlib
import sys

STACK = pathlib.Path("/home/marov/psi-stack")
LAB = "/home/marov/monitoring-lab"


def patch_compose() -> list[str]:
    path = STACK / "docker-compose.yml"
    text = path.read_text(encoding="utf-8")
    done = []

    steps = [
        (
            "enable-remote-write-receiver",
            "      - --web.enable-lifecycle        # даёт POST /-/reload, см. ./wsl-stack.sh reload\n",
            "      - --web.enable-remote-write-receiver   # приёмник метрик из Alloy\n",
            "Prometheus: включён приёмник remote_write",
        ),
        (
            "rules-lab",
            "      - ./prometheus:/etc/prometheus:ro\n",
            f"      - {LAB}/prometheus/rules:/etc/prometheus/rules-lab:ro\n",
            "Prometheus: подключены правила лаборатории",
        ),
        (
            "dashboards/lab",
            "      - ./grafana/dashboards:/var/lib/grafana/dashboards:ro\n",
            f"      - {LAB}/grafana/dashboards:/var/lib/grafana/dashboards/lab:ro\n",
            "Grafana: подключены дашборды лаборатории",
        ),
    ]

    for marker, anchor, addition, message in steps:
        if marker in text:
            continue
        if anchor not in text:
            sys.exit(f"якорь не найден, править вручную: {anchor.strip()}")
        text = text.replace(anchor, anchor + addition, 1)
        done.append(message)

    path.write_text(text, encoding="utf-8")
    return done


def patch_prometheus() -> list[str]:
    path = STACK / "prometheus" / "prometheus.yml"
    text = path.read_text(encoding="utf-8")
    done = []

    if "rules-lab" not in text:
        anchor = "rule_files:\n  - /etc/prometheus/rules/*.yml\n"
        if anchor not in text:
            sys.exit("не найден блок rule_files")
        text = text.replace(anchor, anchor + "  - /etc/prometheus/rules-lab/*.yml\n", 1)
        done.append("prometheus.yml: добавлены правила лаборатории")

    if "alertmanagers" not in text:
        block = (
            "\n# Alertmanager поднимается вместе с Alloy, см. monitoring-lab.\n"
            "alerting:\n"
            "  alertmanagers:\n"
            "    - static_configs:\n"
            '        - targets: ["alertmanager:9093"]\n'
        )
        if "\nscrape_configs:" not in text:
            sys.exit("не найден блок scrape_configs")
        text = text.replace("\nscrape_configs:", block + "\nscrape_configs:", 1)
        done.append("prometheus.yml: подключён Alertmanager")

    path.write_text(text, encoding="utf-8")
    return done


if __name__ == "__main__":
    changes = patch_compose() + patch_prometheus()
    if changes:
        for line in changes:
            print(f"  + {line}")
    else:
        print("  правки уже применены, ничего не изменено")
