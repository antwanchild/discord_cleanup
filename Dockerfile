FROM python:3.14-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY cleanup_bot.py .
COPY config.py .
COPY stats.py .
COPY utils.py .
COPY notifications.py .
COPY cleanup.py .
COPY commands.py .
COPY commands_stats.py .
COPY web.py .
COPY templates/ templates/
COPY VERSION .
COPY healthcheck.py .
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN groupadd -g 1000 botgroup && useradd -u 1000 -g botgroup -s /bin/sh botuser

ENV PUID=1000
ENV PGID=1000
ENV PYTHONPATH=/app
ENV WEB_PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python3 /app/healthcheck.py

ARG VERSION
ARG BUILD_DATE
ARG SOURCE_URL

LABEL org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.title="Discord Cleanup Bot" \
      org.opencontainers.image.description="Automated Discord message cleanup bot" \
      org.opencontainers.image.source="${SOURCE_URL}" \
      org.opencontainers.image.authors="antwanchild"

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "cleanup_bot.py"]
