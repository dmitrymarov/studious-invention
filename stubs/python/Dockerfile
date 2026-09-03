FROM python:3.13-alpine
WORKDIR /app
COPY app.py .
ENV PORT=8000 PYTHONUNBUFFERED=1
EXPOSE 8000
# HEALTHCHECK нужен не мониторингу, а чтобы docker сам метил контейнер
# unhealthy - Alloy забирает это состояние из cAdvisor/docker и красит жёлтым.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD wget -qO- "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1 || exit 1
CMD ["python", "app.py"]
