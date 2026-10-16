# Stage 1: Build React frontend
FROM node:20-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --ignore-scripts 2>/dev/null || npm install
COPY frontend/ ./
RUN npm run build

# Stage 2: Python runtime
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOME=/app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    sqlite3 \
    adb \
    && rm -rf /var/lib/apt/lists/*

# Install cloudflared for optional Cloudflare Tunnel remote access
RUN ARCH=$(dpkg --print-architecture) && \
    curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}" \
    -o /usr/local/bin/cloudflared && \
    chmod +x /usr/local/bin/cloudflared

# Create app directory
WORKDIR $APP_HOME

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY static/ ./static/
COPY entrypoint.sh ./entrypoint.sh
COPY gunicorn.conf.py ./gunicorn.conf.py
COPY scripts/healthcheck.py ./scripts/healthcheck.py

# Copy built frontend into static/spa/
COPY --from=frontend-build /frontend/dist/ ./static/spa/

# Create data directory and home for ADB
RUN mkdir -p /app/data /app/home/.android

# Make scripts executable
RUN chmod +x /app/entrypoint.sh

# Expose port
EXPOSE 5001

# Health check (port, URL base and HTTPS follow the saved settings)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 /app/scripts/healthcheck.py

# Run the application via entrypoint (connects ADB first)
CMD ["/app/entrypoint.sh"]
