# Раннер Ansible в контейнере.
#
# Нужен потому, что ставить ansible в систему может быть некуда (нет root)
# или незачем (разовый прогон). Внутри есть docker-cli: роль управляет
# контейнерами через смонтированный сокет демона хоста.
#
# Для прода это не обязательно - там достаточно обычного
#   ansible-playbook -i inventory.yml site.yml -l prod
FROM alpine:3.22

RUN apk add --no-cache \
      ansible-core \
      docker-cli \
      docker-cli-compose \
      python3 \
      py3-yaml \
      curl

WORKDIR /work
ENTRYPOINT ["ansible-playbook"]
