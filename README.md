# Ecobee Temperature Manager

A Python service that automatically manages your Ecobee thermostat temperature based on a configurable schedule using JWT authentication.

## Features

- JWT-based authentication with Ecobee web portal (via Selenium headless Chrome)
- Configurable temperature schedules with automatic gap-filling for missing hours
- Alternating `sleep`/`smart1` climate program pushed to Ecobee on startup and schedule changes
- 60-minute temperature holds applied on each check cycle
- Health check endpoint
- Automatic token refresh (re-login via Selenium when token expires)
- Persistent rotating logs
- SOPS-encrypted secrets support (`env.secrets.enc`) with plaintext fallback (`env.secrets`)

## Prerequisites

- Docker and Docker Compose
- Ecobee account credentials
- Existing schedule configuration in `config/schedule.json`

## Quick Start

1. **Configure credentials (choose one):**

   Plaintext fallback:
   ```bash
   cp .env.example env.secrets
   # Edit env.secrets with your Ecobee credentials
   ```

   Or SOPS-encrypted (requires [age](https://github.com/FiloSottile/age) and [sops](https://github.com/getsops/sops/releases)):
   ```bash
   sops -e --input-type dotenv --output-type dotenv env.secrets > env.secrets.enc
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
| `CHECK_INTERVAL_MINUTES` | No | 45 | How often to check/update temperature |
| `LOG_LEVEL` | No | INFO | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `SELENIUM_TIMEOUT` | No | 30 | Selenium operation timeout in seconds |
| `SELENIUM_REDIRECT_TIMEOUT` | No | 60 | Redirect wait timeout in seconds |

### Schedule Configuration

Edit `config/schedule.json` to define temperatures for each day and hour. Any missing `HH:00` entries are automatically filled in by carrying forward the last known temperature, and the completed schedule is saved back to the file.

Example with varied temperatures:
```json
{
  "timezone": "America/Chicago",
  "default_temperature": 67,
  "schedule": {
    "monday": [
      { "time": "00:00", "temperature": 65 },
      { "time": "06:00", "temperature": 68 },
      { "time": "09:00", "temperature": 65 },
      { "time": "17:00", "temperature": 68 },
      { "time": "22:00", "temperature": 65 }
    ]
  }
}
```

All 7 days must be defined. Missing hours are filled automatically on startup.

### Ecobee Program Schedule

On startup (and whenever `schedule.json` changes), the service pushes an alternating `sleep`/`smart1` climate program to the Ecobee covering all 24 hours every day. This can also be triggered manually:

```bash
python ecobee_cli.py schedule-night
python ecobee_cli.py schedule-night --dry-run   # preview without applying
```

### Directory Structure

```
.
├── config/
│   └── schedule.json          # Temperature schedule configuration (auto-filled if gaps exist)
├── ecobee_jwt.json            # JWT tokens (auto-generated)
├── env.secrets                # Plaintext credentials (gitignored)
├── env.secrets.enc            # SOPS-encrypted credentials
├── logs/
│   └── ecobee_service.log     # Service logs (rotating, 100MB x 30 files)
├── ecobee_auth_jwt.py         # JWT authentication via Selenium
├── ecobee_cli.py              # Command-line tool for manual thermostat control
├── ecobee_schedule_ui.py      # Selenium-based portal UI helper
├── ecobee_service.py          # Main service daemon
├── health_server.py           # Health check endpoint (port 8080)
├── schedule_engine.py         # Schedule parsing, gap-filling, and lookup
├── secrets_loader.py          # SOPS/plaintext secrets loader
├── temperature_controller.py  # Ecobee API calls (get/set temperature, climates, sensors)
└── requirements.txt           # Python dependencies
```

## CLI Tool

`ecobee_cli.py` provides manual control without running the full service. It reads the JWT from `ecobee_jwt.json`.

```bash
python ecobee_cli.py status           # Show thermostat info
python ecobee_cli.py get              # Get current temperature setting
python ecobee_cli.py set <temp>       # Set temperature hold (°F)
python ecobee_cli.py sensors          # List sensors with temp and occupancy
python ecobee_cli.py lean <temp>      # Select sensors that pull average toward <temp>
python ecobee_cli.py schedule         # Show current Ecobee program schedule (all 24 hours)
python ecobee_cli.py schedule-night   # Push alternating sleep/smart1 program for all 24 hours
python ecobee_cli.py dump-program     # Dump raw program JSON for debugging
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
   ls -la ecobee_jwt.json
   ```

4. **Token save fails on startup:**
   Ensure the working directory is writable. The token is saved as `ecobee_jwt.json` in the current directory.

5. **SOPS decryption fails:**
   Ensure the age private key is available at `~/.config/sops/age/keys.txt` or set `SOPS_AGE_KEY_FILE`.

## Stopping the Service

```bash
docker-compose down
```

## Maintenance

- JWT tokens are automatically refreshed via re-login when expired (checked before every cycle)
- Logs are rotated automatically at 100MB, keeping 30 files
- Schedule gaps are filled automatically on load; `schedule.json` is updated in place
- The Ecobee climate program is re-applied automatically when `schedule.json` changes
