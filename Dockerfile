# syntax=docker/dockerfile:1

# Minimal base image
FROM python:3.11-slim

# Environment safety/perf defaults
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/home/appuser/.local/bin:${PATH}"

# Workdir inside container
WORKDIR /app

# Install Python dependencies first (better layer caching)
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Create non-root user and ensure writable data directory
RUN useradd -m -u 10001 -s /bin/bash appuser && \
    mkdir -p /app/data && \
    chown -R appuser:appuser /app

# Copy the rest of the project
COPY . /app

# Ensure ownership after copy
RUN chown -R appuser:appuser /app

# Expose the Flask/Gunicorn port
EXPOSE 8080

# Drop privileges
USER appuser

# Default command: run behind gunicorn
# Binds on 0.0.0.0:8080; workers and timeout suitable for small deployments
CMD ["gunicorn", "-b", "0.0.0.0:8080", "app:app", "--workers", "3", "--timeout", "120"]
