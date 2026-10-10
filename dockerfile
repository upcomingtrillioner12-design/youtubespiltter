FROM python:3.12-slim

# Install system dependencies: ffmpeg and chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    chromium \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render defaults to port 10000; use $PORT to be safe
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "10000"]
