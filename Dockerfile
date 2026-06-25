FROM python:3.12-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY app.py /app/app.py
COPY sound_mixer_api /app/sound_mixer_api
COPY measure-loudness.md /app/measure-loudness.md
COPY mastering-ui-guide.md /app/mastering-ui-guide.md
COPY voice-gate-guide.md /app/voice-gate-guide.md

ENV LOUDNESS_API_HOST=0.0.0.0
ENV LOUDNESS_API_PORT=8080
ENV LOUDNESS_API_TIMEOUT_SECONDS=60
ENV LOUDNESS_API_DATA_DIR=/data
ENV LOUDNESS_API_PUBLIC_BASE_URL=https://sound-mixer-api.jamrockdev.com

CMD ["python", "/app/app.py"]
