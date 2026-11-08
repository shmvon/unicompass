# ==============================================================================
# UNI Compass — Docker Container
# Runs Flask application via Gunicorn WSGI server.
# Supports 3 deployment modes:
# 1. Local Mac test (standalone container or docker compose)
# 2. CaaS tryout without volume (ephemeral standalone container)
# 3. CaaS production with persistent volume (mounts to /app/data or /volume)
# ==============================================================================

FROM python:3.11-slim

WORKDIR /app

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Setup seed and runtime data directories:
# 1. Protected /app/seeds: Holds baseline seed templates (users.csv, countries.csv)
#    outside /app/data so external volume mounts never shadow them.
# 2. /app/data: Pre-populated with baseline data for standalone use without a volume.
#    When an external volume IS mounted to /app/data, the app automatically seeds
#    the new volume on first startup if empty, and preserves user edits thereafter.
RUN mkdir -p /app/seeds /app/data/exports && \
    (cp /app/seeds/users.csv /app/data/users.csv 2>/dev/null || cp /app/data/users.csv /app/seeds/users.csv 2>/dev/null || true) && \
    (cp /app/seeds/countries.csv /app/data/countries.csv 2>/dev/null || cp /app/data/countries.csv /app/seeds/countries.csv 2>/dev/null || true)

# Expose internal WSGI port (8767) and server port (8000)
EXPOSE 8767
EXPOSE 8000

# Start production WSGI server with Gunicorn bound to [::] (IPv6/IPv4 dual stack) on port 8767
CMD ["gunicorn", "--workers", "4", "--bind", "[::]:8767", "--timeout", "120", "wsgi:app"]

