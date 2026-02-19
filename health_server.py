#!/usr/bin/env python3
"""
Health Server Module
HTTP server for health checks and status monitoring
"""

import logging
from flask import Flask, jsonify
from datetime import datetime
from typing import Dict, Optional
import threading

logger = logging.getLogger(__name__)


class HealthServer:
    """HTTP server for health monitoring"""

    def __init__(self, port: int = 8080):
        self.port = port
        self.app = Flask(__name__)
        self.server_thread = None

        # Service statistics
        self.start_time = datetime.now()
        self.stats = {
            'checks_performed': 0,
            'reverts_performed': 0,
            'token_refreshes': 0,
            'errors': 0,
            'last_check': None,
            'last_revert': None,
            'last_error': None
        }

        # Service state
        self.state = {
            'token_valid': False,
            'token_expires_at': None,
            'refresh_token_expires_at': None,
            'current_temperature': None,
            'expected_temperature': None,
            'schedule_loaded': False
        }

        self._setup_routes()

    def _setup_routes(self):
        """Setup Flask routes"""

        @self.app.route('/health', methods=['GET'])
        def health():
            """Basic health check endpoint"""
            uptime_seconds = (datetime.now() - self.start_time).total_seconds()

            # Determine overall health status
            status = "healthy"
            if not self.state['token_valid']:
                status = "unhealthy"
            elif not self.state['schedule_loaded']:
                status = "degraded"

            response = {
                'status': status,
                'uptime_seconds': int(uptime_seconds),
                'timestamp': datetime.now().isoformat()
            }

            status_code = 200 if status == "healthy" else 503
            return jsonify(response), status_code

        @self.app.route('/status', methods=['GET'])
        def status():
            """Detailed status endpoint"""
            uptime_seconds = (datetime.now() - self.start_time).total_seconds()

            # Calculate token expiry times
            token_expires_in_minutes = None
            refresh_token_expires_in_days = None

            if self.state['token_expires_at']:
                token_delta = (self.state['token_expires_at'] - datetime.now()).total_seconds()
                token_expires_in_minutes = int(token_delta / 60)

            if self.state['refresh_token_expires_at']:
                refresh_delta = (self.state['refresh_token_expires_at'] - datetime.now()).days
                refresh_token_expires_in_days = refresh_delta

            response = {
                'status': 'healthy' if self.state['token_valid'] else 'unhealthy',
                'uptime_seconds': int(uptime_seconds),
                'start_time': self.start_time.isoformat(),
                'current_time': datetime.now().isoformat(),

                # Token status
                'token_status': 'valid' if self.state['token_valid'] else 'invalid',
                'token_expires_in_minutes': token_expires_in_minutes,
                'refresh_token_expires_in_days': refresh_token_expires_in_days,

                # Temperature status
                'current_temperature': self.state['current_temperature'],
                'expected_temperature': self.state['expected_temperature'],
                'temperature_match': (
                    self.state['current_temperature'] == self.state['expected_temperature']
                    if self.state['current_temperature'] and self.state['expected_temperature']
                    else None
                ),

                # Schedule status
                'schedule_loaded': self.state['schedule_loaded'],

                # Statistics
                'checks_performed': self.stats['checks_performed'],
                'reverts_performed': self.stats['reverts_performed'],
                'token_refreshes': self.stats['token_refreshes'],
                'errors': self.stats['errors'],
                'last_check': self.stats['last_check'].isoformat() if self.stats['last_check'] else None,
                'last_revert': self.stats['last_revert'].isoformat() if self.stats['last_revert'] else None,
                'last_error': self.stats['last_error'].isoformat() if self.stats['last_error'] else None
            }

            return jsonify(response)

        @self.app.route('/schedule', methods=['GET'])
        def schedule():
            """Current schedule and expected temperature"""
            response = {
                'schedule_loaded': self.state['schedule_loaded'],
                'current_time': datetime.now().isoformat(),
                'expected_temperature': self.state['expected_temperature'],
                'current_temperature': self.state['current_temperature']
            }

            return jsonify(response)

        @self.app.route('/stats', methods=['GET'])
        def stats():
            """Service statistics"""
            uptime_seconds = (datetime.now() - self.start_time).total_seconds()

            response = {
                'uptime_seconds': int(uptime_seconds),
                'uptime_hours': round(uptime_seconds / 3600, 2),
                'uptime_days': round(uptime_seconds / 86400, 2),
                'checks_performed': self.stats['checks_performed'],
                'reverts_performed': self.stats['reverts_performed'],
                'token_refreshes': self.stats['token_refreshes'],
                'errors': self.stats['errors'],
                'revert_rate': (
                    round(self.stats['reverts_performed'] / self.stats['checks_performed'] * 100, 2)
                    if self.stats['checks_performed'] > 0 else 0
                ),
                'error_rate': (
                    round(self.stats['errors'] / self.stats['checks_performed'] * 100, 2)
                    if self.stats['checks_performed'] > 0 else 0
                )
            }

            return jsonify(response)

    def update_token_status(self, valid: bool, expires_at: Optional[datetime] = None,
                           refresh_expires_at: Optional[datetime] = None):
        """Update token status"""
        self.state['token_valid'] = valid
        if expires_at:
            self.state['token_expires_at'] = expires_at
        if refresh_expires_at:
            self.state['refresh_token_expires_at'] = refresh_expires_at

    def update_temperature_status(self, current: Optional[int], expected: Optional[int]):
        """Update temperature status"""
        self.state['current_temperature'] = current
        self.state['expected_temperature'] = expected

    def update_schedule_status(self, loaded: bool):
        """Update schedule status"""
        self.state['schedule_loaded'] = loaded

    def increment_checks(self):
        """Increment check counter"""
        self.stats['checks_performed'] += 1
        self.stats['last_check'] = datetime.now()

    def increment_reverts(self):
        """Increment revert counter"""
        self.stats['reverts_performed'] += 1
        self.stats['last_revert'] = datetime.now()

    def increment_token_refreshes(self):
        """Increment token refresh counter"""
        self.stats['token_refreshes'] += 1

    def increment_errors(self):
        """Increment error counter"""
        self.stats['errors'] += 1
        self.stats['last_error'] = datetime.now()

    def start(self):
        """Start the health server in a background thread"""
        def run_server():
            # Disable Flask's default logging
            log = logging.getLogger('werkzeug')
            log.setLevel(logging.ERROR)

            logger.info(f"Starting health server on port {self.port}")
            self.app.run(host='0.0.0.0', port=self.port, threaded=True)

        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()
        logger.info(f"Health server started on http://0.0.0.0:{self.port}")

    def is_running(self) -> bool:
        """Check if server is running"""
        return self.server_thread is not None and self.server_thread.is_alive()


if __name__ == "__main__":
    # Test the health server
    logging.basicConfig(level=logging.INFO)

    server = HealthServer(port=8080)
    server.update_token_status(True, datetime.now())
    server.update_temperature_status(68, 68)
    server.update_schedule_status(True)
    server.increment_checks()

    server.start()

    print("Health server running on http://localhost:8080")
    print("Try these endpoints:")
    print("  http://localhost:8080/health")
    print("  http://localhost:8080/status")
    print("  http://localhost:8080/schedule")
    print("  http://localhost:8080/stats")
    print("\nPress Ctrl+C to stop")

    try:
        # Keep main thread alive
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping server...")
