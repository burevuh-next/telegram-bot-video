FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN adduser --disabled-password --gecos "" bot && \
    mkdir -p /data /config && \
    chown -R bot:bot /data /config

USER bot

VOLUME ["/data", "/config"]

ENTRYPOINT ["python", "-m", "app.main"]
