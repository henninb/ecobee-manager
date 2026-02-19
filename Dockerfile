FROM python:3.13-slim

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
COPY ecobee_service.py .
COPY health_server.py .
COPY schedule_engine.py .
COPY secrets_loader.py .
COPY temperature_controller.py .

# Create non-root user with UID/GID 1000 to match typical host user
RUN groupadd -g 1000 ecobee && useradd -u 1000 -g ecobee -d /app -s /sbin/nologin ecobee

# Create necessary directories and placeholder for JWT token
RUN mkdir -p config logs .cache/selenium && touch ecobee_jwt.json && chown -R ecobee:ecobee /app

USER ecobee

# Set environment variables (defaults)
ENV HOME=/app
ENV CHECK_INTERVAL_MINUTES=45
ENV LOG_LEVEL=INFO
ENV SELENIUM_TIMEOUT=30
ENV SELENIUM_REDIRECT_TIMEOUT=60
ENV CHROMIUM_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_BIN=/usr/bin/chromedriver

# Health check endpoint (assuming health_server runs on port 8080)
HEALTHCHECK --interval=60s --timeout=10s --start-period=10s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://localhost:8080/health || exit 1

# Run the service with unbuffered output
CMD ["python", "-u", "ecobee_service.py"]
