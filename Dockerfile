# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8501 \
    TEXTLAB_HOSTED=1 \
    TEXTLAB_SAVE_HISTORY=0
WORKDIR /app

COPY requirements.txt ./
# The optional build secret supplies a managed-network CA bundle without
# retaining session-specific certificates in image layers. Normal hosts omit it.
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then \
      PIP_CERT=/run/secrets/proxy_ca python -m pip install --no-cache-dir -r requirements.txt; \
    else \
      python -m pip install --no-cache-dir -r requirements.txt; \
    fi

RUN groupadd --gid 10001 textlab && useradd --uid 10001 --gid textlab --create-home textlab
COPY --chown=textlab:textlab app.py ./
COPY --chown=textlab:textlab textlab ./textlab
COPY --chown=textlab:textlab .streamlit/config.toml ./.streamlit/config.toml
RUN chown textlab:textlab /app
USER textlab

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import http.client,os; c=http.client.HTTPConnection('127.0.0.1',int(os.environ.get('PORT','8501')),timeout=3); c.request('GET','/_stcore/health'); r=c.getresponse(); raise SystemExit(0 if r.status==200 and r.read()==b'ok' else 1)"
CMD ["sh", "-c", "exec python -m streamlit run app.py --server.address 0.0.0.0 --server.port \"${PORT:-8501}\""]
