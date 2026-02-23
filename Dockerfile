FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY cleanup_bot.py .
COPY VERSION .
CMD ["python", "cleanup_bot.py"]
