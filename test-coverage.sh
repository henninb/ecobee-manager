#!/bin/sh

echo "=== Coverage Report ==="
pytest tests/ \
  --cov=secrets_loader \
  --cov=temperature_controller \
  --cov=schedule_engine \
  --cov=health_server \
  --cov=override_manager \
  --cov=ecobee_auth_jwt \
  --cov=ecobee_cli \
  --cov-report=term-missing \
  --cov-config=pytest.ini \
  --cov-fail-under=90

exit $?
