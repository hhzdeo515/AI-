FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/var/lib/ai-assistant \
    PORT=10000 \
    PUBLIC_SCHEME=https

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 assistant \
    && useradd --uid 10001 --gid assistant --no-create-home assistant

WORKDIR /app/langgraph-app
COPY langgraph-app/requirements.txt langgraph-app/requirements-production.txt ./
RUN python -m pip install --no-cache-dir -r requirements-production.txt

COPY langgraph-app/lg_assistant/ ./lg_assistant/
COPY langgraph-app/production.py ./
RUN mkdir -p /var/lib/ai-assistant \
    && chown assistant:assistant /var/lib/ai-assistant

USER assistant
EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import json,os,urllib.request; result=json.load(urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','10000')+'/health',timeout=4)); assert result['ok'] and result['auth_enabled']"
CMD ["python", "production.py"]
