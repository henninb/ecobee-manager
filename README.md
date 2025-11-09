# Ecobee Temperature Manager

A Python service that automatically manages your Ecobee thermostat temperature based on a configurable schedule using JWT authentication.

## Features

- JWT-based authentication with Ecobee web portal
- Configurable temperature schedules
- Health check endpoint
- Automatic token refresh
- Persistent logging

## Prerequisites

- Docker and Docker Compose
- Ecobee account credentials
- Existing schedule configuration in `config/schedule.json`

## Quick Start

1. **Copy environment file and configure credentials:**
   ```bash
   cp .env.example .env
   # Edit .env with your Ecobee credentials
   ```

2. **Build and run with Docker Compose:**
   ```bash
   docker-compose up -d
   ```

3. **View logs:**
   ```bash
   docker-compose logs -f
   ```

## Manual Docker Build

```bash
# Build the image
docker build -t ecobee-temperature-manager .

# Run the container
docker run -d \
  --name ecobee-temperature-manager \
  -e ECOBEE_EMAIL=your.email@example.com \
  -e ECOBEE_PASSWORD=your_password \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/config:/app/config:ro \
  ecobee-temperature-manager
```

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ECOBEE_EMAIL` | Yes | - | Your Ecobee account email |
| `ECOBEE_PASSWORD` | Yes | - | Your Ecobee account password |
| `CHECK_INTERVAL_MINUTES` | No | 10 | How often to check/update temperature |
| `LOG_LEVEL` | No | INFO | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `SELENIUM_TIMEOUT` | No | 30 | Selenium operation timeout in seconds |
| `SELENIUM_REDIRECT_TIMEOUT` | No | 60 | Redirect wait timeout in seconds |

### Directory Structure

```
.
├── config/
│   └── schedule.json       # Temperature schedule configuration
├── data/
│   └── .ecobee_jwt.json   # JWT tokens (auto-generated)
├── logs/
│   └── ecobee_service.log # Service logs
├── ecobee_auth_jwt.py     # JWT authentication handler
├── ecobee_service_jwt.py  # Main service
├── health_server.py       # Health check endpoint
├── schedule_engine.py     # Schedule management
├── temperature_controller.py  # Temperature control logic
└── requirements.txt       # Python dependencies
```

## Health Check

The service exposes a health endpoint on port 8080:
```bash
curl http://localhost:8080/health
```

## Troubleshooting

1. **Check container logs:**
   ```bash
   docker-compose logs -f ecobee-temperature-manager
   ```

2. **Check service logs:**
   ```bash
   tail -f logs/ecobee_service.log
   ```

3. **Verify JWT token:**
   ```bash
   ls -la data/.ecobee_jwt.json
   ```

## Stopping the Service

```bash
docker-compose down
```

## Maintenance

- JWT tokens are automatically refreshed when expired
- Logs are rotated automatically (configured in code)
- Schedule can be updated by modifying `config/schedule.json` and restarting the service
