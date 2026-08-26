# ==============================================================================
# UNI Compass — Docker Container
# Runs Flask application via Gunicorn WSGI server with persistent /app/data mount.
# ==============================================================================

FROM python:3.11-slim

WORKDIR /app

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/app/data

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Ensure data volume mount point exists
RUN mkdir -p /app/data/exports

# Expose HTTP port
EXPOSE 8000

# Start production WSGI server with Gunicorn
CMD ["gunicorn", "--workers", "4", "--bind", "0.0.0.0:8000", "--timeout", "120", "wsgi:app"]
