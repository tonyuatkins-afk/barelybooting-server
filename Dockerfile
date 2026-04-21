FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BAREBOOT_DB=/data/barelybooting.sqlite \
    PORT=8080

WORKDIR /app

RUN adduser --disabled-password --gecos "" --uid 1000 app \
    && mkdir -p /data \
    && chown app:app /data

COPY requirements.txt requirements-lock.txt ./
# Install from the lock file so the dependency closure is byte-for-byte
# reproducible. requirements.txt stays as the human-maintained source
# of truth; requirements-lock.txt is regenerated from pip freeze.
RUN pip install --no-cache-dir -r requirements-lock.txt

COPY barelybooting ./barelybooting

USER app
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/v1/health',timeout=3).status==200 else 1)"

CMD ["sh", "-c", "python -m barelybooting init-db && exec python -m waitress --host=0.0.0.0 --port=${PORT} --threads=8 --connection-limit=100 --channel-timeout=30 --call barelybooting:create_app"]
