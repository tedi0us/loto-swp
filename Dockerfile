FROM python:3.12-slim

WORKDIR /app

# Non-root user for runtime — drop privileges before CMD
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

# Ghostscript — flattens SWP PDF form fields into static page content
# LibreOffice headless — converts LOTO Excel workbook to PDF
# fonts-recommended — provides Helvetica metrics (standard Type1) for GS
RUN apt-get update && apt-get install -y --no-install-recommends \
      ghostscript \
      libreoffice-calc \
      libreoffice-writer \
      fonts-recommended \
      cups-client \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer caching).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# instance/ holds the SQLite DB and secret_key file — must be a volume mount at runtime.
RUN mkdir -p instance && chown -R appuser:appgroup instance

EXPOSE 8000

# Healthcheck uses stdlib only — no curl needed in the slim image
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')" || exit 1

USER appuser

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-2} --preload --timeout 120 --access-logfile - --limit-request-line 8190 'app:create_app()'"]
