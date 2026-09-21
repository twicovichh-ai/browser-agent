# Playwright image: Chromium + all system deps + Xvfb. Version must match requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent ./agent
COPY webapp ./webapp
COPY demo ./demo
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV IN_DOCKER=1 DISPLAY=:99 HOST=0.0.0.0 PORT=8080 PROFILE_DIR=/data/profile \
    WINDOW_SIZE=1280,860 PYTHONUNBUFFERED=1
EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
