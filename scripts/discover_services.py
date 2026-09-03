#!/usr/bin/env python3
"""Черновик mon_services из работающих контейнеров.

Запускается на сервере. Смотрит, какие контейнеры подняты, какие порты они
открывают, и ПРОВЕРЯЕТ здоровье-эндпоинты на самом деле - изнутри docker-сети,
одним вспомогательным контейнером на сеть. То, что ответило 2xx, попадает в
http-пробу; остальное - в tcp_connect.

Результат нужно вычитать руками: группы угаданы по имени образа, а часть
сервисов может быть служебной и мониторинга не заслуживать.

    python3 discover_services.py > services.yml
"""
import json
import re
import subprocess
import sys

# Порты, для которых известен путь проверки здоровья.
KNOWN_HTTP = {
    9200: ["/_cluster/health"],
    9600: ["/actuator/health", "/ready"],
    5701: ["/hazelcast/health/node-state"],
    9000: ["/health/ready", "/health", "/metrics"],
    8080: ["/health", "/healthz", "/actuator/health", "/health/ready"],
    8081: ["/health", "/healthz", "/actuator/health"],
    8000: ["/health", "/healthz"],
    80: ["/health", "/healthz"],
    3000: ["/api/health", "/health"],
    9090: ["/-/healthy"],
    9093: ["/-/healthy"],
}

# Порты, которые заведомо не HTTP - для них сразу tcp_connect.
TCP_ONLY = {6379, 5432, 3306, 5672, 9092, 11211, 26500, 26501, 27017, 1521}

# Порты, которые сами по себе не сервис, а служебка.
SKIP_PORTS = {9100, 8443}

# Пути метрик проверяются наравне с health: без этого у Zeebe
# /actuator/prometheus не попал бы в кандидаты и metrics потерялись бы.
METRICS_PATHS = {9600: "/actuator/prometheus"}
DEFAULT_METRICS_PATH = "/metrics"

GROUP_BY_IMAGE = [
    (r"redis|elasticsearch|keycloak|postgres|mysql|mariadb|vault", "infra"),
    (r"zeebe|camunda|hazelcast|kafka|rabbitmq", "zeebe"),
]

EXPORTER_BY_IMAGE = [
    (r"redis", "redis"),
    (r"elasticsearch", "elasticsearch"),
]


def sh(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True).stdout.strip()


def containers() -> list[dict]:
    """Запущенные контейнеры с сетями и открытыми портами."""
    names = [n for n in sh(["docker", "ps", "--format", "{{.Names}}"]).splitlines() if n]
    out = []
    for name in names:
        raw = sh(["docker", "inspect", name])
        if not raw:
            continue
        info = json.loads(raw)[0]
        nets = sorted(info["NetworkSettings"]["Networks"])
        ports = sorted(
            int(p.split("/")[0])
            for p in (info["Config"].get("ExposedPorts") or {})
            if p.endswith("/tcp")
        )
        out.append(
            {
                "name": name,
                "image": info["Config"]["Image"],
                "networks": nets,
                "ports": [p for p in ports if p not in SKIP_PORTS],
            }
        )
    return out


def health_paths(port: int) -> list[str]:
    """Пути проверки здоровья для порта, в порядке предпочтения.

    Путь метрик сюда НЕ входит: он тоже отвечает 2xx, и попав в пробу,
    маскировал бы отсутствие настоящего health-эндпоинта.
    """
    return list(KNOWN_HTTP.get(port, ["/health", "/healthz"]))


def guess(patterns: list[tuple[str, str]], image: str) -> str | None:
    for rx, value in patterns:
        if re.search(rx, image, re.I):
            return value
    return None


def check_urls(network: str, urls: list[str]) -> dict[str, int]:
    """Проверяет пачку URL одним контейнером в нужной сети.

    По контейнеру на КАЖДЫЙ url было бы 100+ запусков docker run на средний
    сервер, поэтому весь список проверяется одним shell-циклом внутри.
    """
    if not urls:
        return {}
    script = 'for u in "$@"; do printf "%s %s\\n" "$u" "$(curl -s -o /dev/null -w %{http_code} --max-time 3 "$u" 2>/dev/null || echo 000)"; done'
    raw = sh(
        ["docker", "run", "--rm", "--network", network, "--entrypoint", "sh",
         "curlimages/curl:latest", "-c", script, "--"] + urls
    )
    result = {}
    for line in raw.splitlines():
        parts = line.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].isdigit():
            result[parts[0]] = int(parts[1])
    return result


def build_candidates(cs: list[dict]) -> dict[str, list[str]]:
    """URL-кандидаты, сгруппированные по сети - чтобы проверять пачками."""
    by_net: dict[str, list[str]] = {}
    for c in cs:
        if not c["networks"]:
            continue
        net = c["networks"][0]
        for port in c["ports"]:
            if port in TCP_ONLY:
                continue
            paths = health_paths(port)
            paths.append(METRICS_PATHS.get(port, DEFAULT_METRICS_PATH))
            for path in paths:
                by_net.setdefault(net, []).append(f"http://{c['name']}:{port}{path}")
    return by_net


def main() -> None:
    cs = containers()
    if not cs:
        sys.exit("Запущенных контейнеров не найдено.")

    print("# Черновик, сгенерированный scripts/discover_services.py.", file=sys.stderr)
    print(f"# Контейнеров: {len(cs)}. Проверяю здоровье-эндпоинты...", file=sys.stderr)

    checks: dict[str, int] = {}
    for net, urls in build_candidates(cs).items():
        checks.update(check_urls(net, urls))

    ok = {u for u, code in checks.items() if 200 <= code < 300}
    print(f"# Ответили 2xx: {len(ok)} из {len(checks)}", file=sys.stderr)

    print("mon_services:")
    for c in sorted(cs, key=lambda x: x["name"]):
        if not c["ports"]:
            print(f"  # {c['name']}: не открывает портов, пропущен")
            continue

        group = guess(GROUP_BY_IMAGE, c["image"]) or "apps"
        probe = None
        for port in c["ports"]:
            for path in health_paths(port):
                url = f"http://{c['name']}:{port}{path}"
                if url in ok:
                    probe = ("http_2xx", url)
                    break
            if probe:
                break
        if probe is None:
            probe = ("tcp_connect", f"{c['name']}:{c['ports'][0]}")

        print(f"  - name: {c['name']}")
        print(f"    group: {group}")
        print(f"    probe: {{module: {probe[0]}, address: \"{probe[1]}\"}}")

        for port in c["ports"]:
            mpath = METRICS_PATHS.get(port, DEFAULT_METRICS_PATH)
            murl = f"http://{c['name']}:{port}{mpath}"
            if murl in ok:
                print(f"    metrics: {{address: \"{c['name']}:{port}\", path: {mpath}}}")
                break

        exporter = guess(EXPORTER_BY_IMAGE, c["image"])
        if exporter == "redis":
            print("    exporter: redis")
            print(f"    exporter_address: \"{c['name']}:6379\"")
        elif exporter == "elasticsearch":
            print("    exporter: elasticsearch")
            print(f"    exporter_address: \"http://{c['name']}:9200\"")

        if probe[0] == "tcp_connect":
            print("    # tcp_connect не отличит живой сервис от зависшего -")
            print("    # добавьте health-эндпоинт, если он есть")


if __name__ == "__main__":
    main()
