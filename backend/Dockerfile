FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Railway assigns $PORT dynamically (typically 8080). Use it so the platform
# health check can reach the app. Default to 8000 for local Docker Compose.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}