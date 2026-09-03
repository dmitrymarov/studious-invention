FROM alpine:3 AS build
RUN apk add --no-cache g++ musl-dev
WORKDIR /src
COPY main.cpp .
# Статическая сборка: рантайм-образу не нужен ни libstdc++, ни musl.
RUN g++ -O2 -std=c++17 -static -pthread -o service main.cpp && strip service

FROM alpine:3
RUN apk add --no-cache wget
COPY --from=build /src/service /usr/local/bin/service
ENV PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD wget -qO- "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1 || exit 1
ENTRYPOINT ["/usr/local/bin/service"]
