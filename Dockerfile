FROM python:3.14-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY cleanup_bot.py .
COPY VERSION .

ARG VERSION
ARG BUILD_DATE
LABEL org.opencontainers.image.version=$VERSION
LABEL org.opencontainers.image.created=$BUILD_DATE
LABEL org.opencontainers.image.title="Discord Cleanup Bot"
LABEL org.opencontainers.image.description="Automated Discord message cleanup bot"
LABEL org.opencontainers.image.source="https://github.com/antwanchild/discord_cleanup"

CMD ["python", "cleanup_bot.py"]
