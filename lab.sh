#!/usr/bin/env bash
# Управление тестовой средой мониторинга.
#
#   ./lab.sh up        поднять среду и мониторинг целиком
#   ./lab.sh deploy    только мониторинг (Ansible), среду не трогать
#   ./lab.sh down      погасить среду и агент (psi-stack не трогается)
#   ./lab.sh status    светофор в терминале - то же, что на дашборде
#   ./lab.sh check     проверить агент и запросы всех панелей
#   ./lab.sh chaos ... сломать/починить сервис для проверки цветов
#   ./lab.sh urls      куда смотреть
set -euo pipefail

cd "$(dirname "$0")"
LAB_DIR="$(pwd)"

LAB_COMPOSE=(docker compose -f docker-compose.yml)
MON_COMPOSE=(docker compose -f docker-compose.monitoring.yml)
PSI_DIR=/home/marov/psi-stack
PROM=http://127.0.0.1:9090
ALLOY=http://127.0.0.1:12345

usage() { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; }

# Ansible запускается в контейнере: ставить его в систему может быть некуда
# (нет root) или незачем. Каталог монтируется по тому же пути, что на хосте,
# иначе сгенерированный compose сошлётся на несуществующие пути.
ansible_run() {
  docker image inspect lab/ansible:local >/dev/null 2>&1 || {
    echo "==> собираю образ с Ansible"
    docker build -q -t lab/ansible:local "$LAB_DIR/ansible" >/dev/null
  }
  docker run --rm \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "$LAB_DIR:$LAB_DIR" \
    -w "$LAB_DIR/ansible" \
    --network host \
    lab/ansible:local site.yml -l lab "$@"
}

cmd_deploy() { ansible_run "$@"; }

cmd_up() {
  echo "==> тестовая среда"
  "${LAB_COMPOSE[@]}" up -d

  echo "==> psi-stack (монтирования правил и дашбордов, remote_write)"
  ( cd "$PSI_DIR" && docker compose up -d )

  echo "==> мониторинг через Ansible"
  ansible_run

  echo
  echo "Zeebe и Elasticsearch прогреваются до минуты - первые полминуты"
  echo "они могут быть красными, это нормально."
  cmd_urls
}

cmd_down() {
  [[ -f docker-compose.monitoring.yml ]] && "${MON_COMPOSE[@]}" down
  "${LAB_COMPOSE[@]}" down
  echo "psi-stack оставлен работать - гасите его отдельно, если нужно."
}

# Светофор прямо в терминале: быстро понять состояние, не открывая браузер,
# и незаменимо при отладке самих правил.
cmd_status() {
  local raw
  raw=$(curl -sf --get "$PROM/api/v1/query" --data-urlencode "query=lab:service_status") || {
    echo "Prometheus недоступен на $PROM" >&2
    return 1
  }

  python3 - "$raw" <<'PY'
import json, sys

rows = json.loads(sys.argv[1]).get("data", {}).get("result", [])
if not rows:
    print("Нет данных: правила ещё не посчитаны или Alloy не пишет в Prometheus.")
    sys.exit(0)

paint = {
    "0": ("\033[41m\033[97m", "НЕДОСТУПЕН"),
    "1": ("\033[43m\033[30m", "ДЕГРАДАЦИЯ"),
    "2": ("\033[42m\033[30m", "РАБОТАЕТ  "),
}

groups = {}
for r in rows:
    m = r["metric"]
    groups.setdefault(m.get("group", "?"), []).append((m.get("service", "?"), r["value"][1]))

for group in ("infra", "zeebe", "workers", "apps"):
    if group not in groups:
        continue
    print(f"\n[{group}]")
    for service, value in sorted(groups[group]):
        color, text = paint.get(value.split(".")[0], ("\033[47m\033[30m", "НЕИЗВЕСТНО"))
        print(f"  {color} {text} \033[0m  {service}")
print()
PY
}

# Проверка сверху вниз: жив агент -> здоровы компоненты -> цели найдены ->
# данные доезжают -> панели дашборда возвращают серии.
cmd_check() {
  printf "  Alloy готов:            "
  curl -s -o /dev/null -w "%{http_code}\n" "$ALLOY/-/ready"

  printf "  компоненты:             "
  curl -sf "$ALLOY/api/v0/web/components" | python3 -c \
    'import json,sys; c=json.load(sys.stdin); bad=[x["id"] for x in c if x["health"]["state"] not in ("healthy","unknown")]; print(f"{len(c)} шт, проблемных: {len(bad)}"); [print("     ", b) for b in bad]'

  echo "  цели по пулам:"
  curl -sf "$ALLOY/metrics" | grep "^prometheus_target_scrape_pool_targets" |
    sed -E 's/.*scrape_job="([^"]+)".*\} (.+)/     \1: \2/'

  echo "  доставка в Prometheus:"
  curl -sf "$ALLOY/metrics" | grep -E "^prometheus_remote_storage_samples_(total|failed_total)" |
    sed -E 's/^([a-z_]+)\{.*\} (.+)$/     \1 = \2/'

  printf "  возраст данных:         "
  curl -sf --get "$PROM/api/v1/query" \
    --data-urlencode 'query=time() - max(timestamp(up{collector="alloy"}))' |
    python3 -c 'import json,sys
r = json.load(sys.stdin)["data"]["result"]
print("%.0f сек назад" % float(r[0]["value"][1]) if r else "ДАННЫХ НЕТ")'

  echo "  панели дашборда:"
  python3 scripts/check_dashboard.py | sed 's/^/  /'
}

# Ломаем сервисы, чтобы проверить, что дашборд честно краснеет и желтеет.
cmd_chaos() {
  local action="${1:-}" target="${2:-}"
  case "$action" in
    degrade|down|ok)
      [[ -n $target ]] || { echo "укажите сервис: ./lab.sh chaos $action py-worker-03" >&2; return 1; }
      docker exec "monlab-$target" wget -qO- "http://127.0.0.1:8000/chaos/$action" && echo
      ;;
    stop|start)
      [[ -n $target ]] || { echo "укажите сервис" >&2; return 1; }
      docker "$action" "monlab-$target" >/dev/null && echo "$target: $action выполнен"
      ;;
    reset)
      "${LAB_COMPOSE[@]}" start >/dev/null
      for c in $(docker ps --filter label=lab.monitor=true --format '{{.Names}}'); do
        docker exec "$c" wget -qO- http://127.0.0.1:8000/chaos/ok >/dev/null 2>&1 || true
      done
      echo "среда возвращена в исходное состояние"
      ;;
    *)
      cat <<'TXT'
Проверка цветов дашборда:

  ./lab.sh chaos degrade py-worker-03   жёлтый: сервис жив, но жалуется
  ./lab.sh chaos down    py-service-01  красный: /health отвечает 503
  ./lab.sh chaos stop    cs-service-02  красный: контейнера нет вообще
  ./lab.sh chaos ok      py-worker-03   вернуть в норму
  ./lab.sh chaos start   cs-service-02  поднять обратно
  ./lab.sh chaos reset                  вернуть всё

Цвет меняется в течение 15-30 секунд: столько занимают проба и правило.
TXT
      ;;
  esac
}

cmd_urls() {
  cat <<'TXT'

  Grafana       http://127.0.0.1:3000    -> папка lab, дашборд "Статус сервисов"
                (том grafana-data существовал до лаборатории - пароль ваш прежний)
  Prometheus    http://127.0.0.1:9090
  Alertmanager  http://127.0.0.1:9093
  Alloy UI      http://127.0.0.1:12345   (граф компонентов и состояние целей)
TXT
}

case "${1:-}" in
  up)     cmd_up ;;
  deploy) shift; cmd_deploy "$@" ;;
  down)   cmd_down ;;
  status) cmd_status ;;
  check)  cmd_check ;;
  urls)   cmd_urls ;;
  chaos)  shift; cmd_chaos "$@" ;;
  *)      usage ;;
esac
