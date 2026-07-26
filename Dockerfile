FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git nodejs npm \
    && rm -rf /var/lib/apt/lists/*

COPY DownloadScript/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY DownloadScript/package.json /app/package.json
RUN npm install --omit=dev

COPY DownloadScript/telegram_downloader.py /app/telegram_downloader.py
COPY DownloadScript/musicn_auto_downloader.mjs /app/musicn_auto_downloader.mjs

CMD ["python", "/app/telegram_downloader.py"]
