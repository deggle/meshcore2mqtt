FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY meshcore2mqtt.py .

RUN useradd -r -u 1000 bridge
USER bridge

CMD ["python", "meshcore2mqtt.py"]
