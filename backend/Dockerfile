FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Listen on 8000 always (internal service-to-service). Railway's $PORT is for
# public health checks and goes through the platform gateway — internal
# networking between services uses the fixed port.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]