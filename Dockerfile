FROM python:3.14-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY cleanup_bot.py .
COPY VERSION .

ARG VERSION
LABEL org.opencontainers.imge.version=$VERSION
CMD ["python", "cleanup_bot.py"]
