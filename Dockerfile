FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp usa un runtime JS (deno) para resolver YouTube
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
ENV MUSIC_DIR=/music CONFIG_DIR=/config PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
