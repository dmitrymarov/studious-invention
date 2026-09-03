# Роль `monitoring`

Светофор доступности сервисов в docker. Один агент Grafana Alloy пробит
сервисы и собирает метрики, Prometheus считает статус recording-правилами,
Grafana рисует плитки «имя сервиса + цвет».

Роль **не разворачивает** Prometheus, Grafana и cAdvisor — предполагается,
что они уже есть. Она разворачивает только Alloy (и, по желанию,
Alertmanager) и кладёт правила с дашбордом туда, откуда их читают.

Зависимостей нет: используется только `ansible.builtin`.

## Что требуется от сервера

1. Docker и `docker compose`.
2. Prometheus с `--web.enable-remote-write-receiver` — Alloy пушит метрики.
3. Prometheus с `--web.enable-lifecycle` — иначе правила подхватятся только
   после его перезапуска.
4. `mon_rules_dir` смонтирован в Prometheus, `mon_dashboards_dir` — в Grafana.
5. Сети из `mon_networks` существуют.

Пункты 2, 3 и 5 роль проверяет сама и останавливается с внятной ошибкой,
если что-то не так. Отключается через `mon_preflight: false`.

## Переменные

### Обязательные

| Переменная | Что это |
|---|---|
| `mon_networks` | docker-сети, в которые включается Alloy: сеть сервисов и сеть Prometheus |
| `mon_services` | список сервисов, см. ниже |

Всё остальное имеет умолчания.

### Описание сервиса

```yaml
mon_services:
  - name: my-worker-01          # обязательно: подпись и метка service
    group: workers              # обязательно: колонка дашборда
    kind: worker                # необязательно: метка kind
    probe:                      # обязательно: чем проверять доступность
      module: http_2xx          #   http_2xx | http_any | tcp_connect
      address: "http://my-worker-01:8080/health"
    metrics:                    # необязательно: свои метрики сервиса
      address: "my-worker-01:8080"
      path: /metrics
    exporter: redis             # необязательно: родной экспортер Alloy
    exporter_address: "redis:6379"
```

Адреса — такие, какими их видит Alloy изнутри docker-сети.

Поддерживаемые экспортеры: `redis`, `elasticsearch`. Добавить свой —
дописать пару в `mon_exporter_up_metrics` (какая метрика означает
«сервис доступен») и ветку в `templates/config.alloy.j2`.

### Часто переопределяемые

| Переменная | Умолчание | Смысл |
|---|---|---|
| `mon_prefix` | `svc` | префикс правил: `svc:service_status` |
| `mon_config_dir` | `/opt/monitoring` | куда кладутся конфиги |
| `mon_rules_dir` | `{{ mon_config_dir }}/prometheus/rules` | должен быть смонтирован в Prometheus |
| `mon_dashboards_dir` | `{{ mon_config_dir }}/grafana/dashboards` | должен быть смонтирован в Grafana |
| `mon_remote_write_url` | `http://prometheus:9090/api/v1/write` | Prometheus глазами Alloy |
| `mon_prometheus_api_url` | `http://127.0.0.1:9090` | Prometheus глазами Ansible |
| `mon_datasource_uid` | `prometheus` | uid датасорса в Grafana |
| `mon_deploy_alertmanager` | `false` | на сервере он обычно уже есть |
| `mon_groups` | `[]` | раскладка панелей; пусто — вывести из сервисов |

Пороги: `mon_slow_probe_seconds` (2), `mon_disk_warn_ratio` (0.8),
`mon_disk_crit_ratio` (0.9), `mon_disk_free_warn_gib` (50),
`mon_disk_free_crit_gib` (10).

## Раскладка дашборда

Если `mon_groups` не задана, роль выводит колонки из `mon_services`: по
строке на группу во всю ширину, в порядке первого появления группы. Задавать
явно нужно только для многоколоночной раскладки:

```yaml
mon_groups:
  - {key: infra,   title: "",              width: 6,  height: 3}
  - {key: zeebe,   title: "",              width: 10, height: 3}
  - {key: apps,    title: "",              width: 8,  height: 3}
  - {key: workers, title: "Воркеры Zeebe", width: 24, height: 4}
```

`width` — доля из 24 колонок Grafana; панели выкладываются слева направо и
переносятся на новую строку, когда не помещаются. Пустой `title` убирает
заголовок панели.

## Что генерируется

| Файл | Из чего |
|---|---|
| `{{ mon_config_dir }}/alloy/config.alloy` | пробы, скрейпы, экспортеры |
| `{{ mon_config_dir }}/alloy/blackbox.yml` | модули проб |
| `{{ mon_rules_dir }}/service-status.yml` | светофор |
| `{{ mon_rules_dir }}/service-alerts.yml` | алерты |
| `{{ mon_dashboards_dir }}/<uid>.json` | дашборд |
| `{{ mon_config_dir }}/docker-compose.monitoring.yml` | контейнер агента |

Перед перезапуском агента роль проверяет синтаксис конфига Alloy, правила
через `promtool` и валидность JSON дашборда, а после подъёма ждёт ответа
от `/-/ready`.

## Теги

| Тег | Что делает |
|---|---|
| без тегов | полная раскатка |
| `--skip-tags deploy` | только перегенерировать конфиги, агент не трогать |
| `--tags deploy` | только поднять агент |

## Пример

```yaml
- hosts: monitoring
  roles:
    - role: monitoring
      vars:
        mon_networks: [app_net, monitoring_net]
        mon_services:
          - name: redis
            group: infra
            probe: {module: tcp_connect, address: "redis:6379"}
            exporter: redis
            exporter_address: "redis:6379"
```
