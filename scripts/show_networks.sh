#!/usr/bin/env bash
# Показывает, кто в какой docker-сети. Запустите на сервере, чтобы понять,
# какие сети перечислять в mon_networks: Alloy обязан делить сеть с каждым
# сервисом, который он пробит, и с Prometheus.
set -euo pipefail

echo "=== кто в какой сети ==="
for n in $(docker network ls --format '{{.Name}}' | grep -vE '^(host|none)$'); do
  members=$(docker network inspect "$n" --format '{{range .Containers}}{{.Name}} {{end}}')
  internal=$(docker network inspect "$n" --format '{{.Internal}}')
  printf '\n%s' "$n"
  [ "$internal" = "true" ] && printf ' (internal: без выхода наружу)'
  printf '\n'
  if [ -z "${members// }" ]; then
    echo "    (пусто)"
  else
    for m in $members; do echo "    $m"; done
  fi
done

echo
echo "=== в каких сетях Prometheus ==="
for c in $(docker ps --format '{{.Names}}' | grep -i prometheus || true); do
  printf '  %s: ' "$c"
  docker inspect "$c" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
done

echo
echo "mon_networks = сеть(и) сервисов + сеть Prometheus."
