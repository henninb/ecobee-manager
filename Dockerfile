FROM python:3.14-slim

# Set working directory
WORKDIR /app

# Install system dependencies for Chrome/Selenium (if needed for token refresh)
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    chromium \
    chromium-driver \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Debian trixie's current chromium (150.0.7871.46-1~deb13u1) crashes on startup
# with SIGILL/UD2 in the browser process on this deployment's host — confirmed
# by bisecting against 149.0.7827.196-1~deb13u1, which runs cleanly. Downgrade
# in place until a working trixie chromium build is available.
RUN cd /tmp && \
    wget -q -O chromium-sandbox.deb https://snapshot.debian.org/file/52dbf5c3edb4e7e4e2ea6e10b655f738fb962617 && \
    wget -q -O chromium-common.deb https://snapshot.debian.org/file/baabe01daaf628d599e14cf331d8b7cd1453e384 && \
    wget -q -O chromium.deb https://snapshot.debian.org/file/122a1721a282240e07dcc9f8f769d0a40361b789 && \
    wget -q -O chromium-driver.deb https://snapshot.debian.org/file/c1db873fb82b0925cc54bc63a0755b635af8cf4d && \
    dpkg -i chromium-sandbox.deb chromium-common.deb chromium.deb chromium-driver.deb && \
    rm -f /tmp/*.deb

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY ecobee_auth_jwt.py .
COPY ecobee_service.py .
COPY health_server.py .
COPY override_manager.py .
COPY schedule_engine.py .
COPY secrets_loader.py .
COPY temperature_controller.py .

# Create non-root user with UID/GID 1000 to match typical host user
RUN groupadd -g 1000 ecobee && useradd -u 1000 -g ecobee -d /app -s /sbin/nologin ecobee

# Create necessary directories and placeholder for JWT token
RUN mkdir -p config logs .cache/selenium && touch ecobee_jwt.json override.json && chown -R ecobee:ecobee /app

USER ecobee

# Set environment variables (defaults)
ENV TZ=America/Chicago
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
