FROM python:3.12-slim

# Non-root user for security
RUN addgroup --gid 1000 app && adduser --uid 1000 --gid 1000 --disabled-password --gecos "" app

WORKDIR /ac-controller

# Install deps before copying source (cache-friendly)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY app/ ./app/

# Create data dir (SQLite lives here, bind-mounted from host)
RUN mkdir -p /data && chown app:app /data

USER app

EXPOSE 8000

# /data is the persistent volume path
# config.yaml is optional — mount one at /data/config.yaml to override defaults
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
