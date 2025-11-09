FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies for Chrome/Selenium (if needed for token refresh)
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY ecobee_auth_jwt.py .
COPY ecobee_service_jwt.py .
COPY health_server.py .
COPY schedule_engine.py .
COPY temperature_controller.py .

# Create necessary directories
RUN mkdir -p config data logs

# Copy configuration if exists (optional - can be mounted)
COPY config/ config/ 2>/dev/null || true

# Set environment variables (defaults)
ENV CHECK_INTERVAL_MINUTES=10
ENV LOG_LEVEL=INFO
ENV SELENIUM_TIMEOUT=30
ENV SELENIUM_REDIRECT_TIMEOUT=60

# Health check endpoint (assuming health_server runs on port 8080)
HEALTHCHECK --interval=60s --timeout=10s --start-period=10s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://localhost:8080/health || exit 1

# Run the service with unbuffered output
CMD ["python", "-u", "ecobee_service_jwt.py"]
