FROM python:3.14-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    su-exec \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY cleanup_bot.py .
COPY VERSION .
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Default PUID/PGID — overridden at runtime via environment variables
RUN groupadd -g 1000 botgroup && useradd -u 1000 -g botgroup -s /bin/sh botuser

ENV PUID=1000
ENV PGID=1000

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD test -f /tmp/health && \
    python3 -c "
from datetime import datetime
with open('/tmp/health') as f:
    ts = datetime.fromisoformat(f.read().strip())
age = (datetime.now() - ts).total_seconds()
exit(0 if age < 300 else 1)
"

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
