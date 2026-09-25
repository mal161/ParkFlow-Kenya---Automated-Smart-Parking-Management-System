"""
HTTP client for the Flask parking engine.

Architecture: Browser -> Django (web) -> Flask (algorithms) -> Supabase.
Django views are thin proxies: they validate input, call the Flask API,
optionally mirror the result into the local reporting database, and
forward the Flask response (JSON body + status code) to the browser.

Uses only the standard library (urllib) so no new dependencies are
needed. The base URL comes from the FLASK_API_URL Django setting
(default http://localhost:5001, see run_flask.py).
"""

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

API_PREFIX = '/api/v1'


class FlaskAPIError(Exception):
    """The Flask API could not be reached (connection/timeout problem)."""


def base_url() -> str:
    return getattr(settings, 'FLASK_API_URL', 'http://localhost:5001').rstrip('/')


def request(method: str, path: str, payload=None, timeout: float = 5.0):
    """
    Call the Flask API.

    Args:
        method: HTTP method
        path: Path including the API prefix, e.g. '/api/v1/parking/entry'
        payload: JSON-serializable body for POST requests
        timeout: Seconds before giving up

    Returns:
        (json_body, status_code)

    Raises:
        FlaskAPIError: when the API is unreachable.
    """
    url = base_url() + path
    body = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8')), resp.status
    except urllib.error.HTTPError as exc:
        # Flask error responses still carry a JSON body worth forwarding.
        try:
            return json.loads(exc.read().decode('utf-8')), exc.code
        except ValueError:
            raise FlaskAPIError(
                f'Flask API returned HTTP {exc.code} for {method} {path}'
            )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FlaskAPIError(f'Flask API unreachable at {base_url()}: {exc}')


def get(path: str, timeout: float = 5.0):
    return request('GET', path, None, timeout)


def post(path: str, payload, timeout: float = 5.0):
    return request('POST', path, payload, timeout)


def unavailable(exc: Exception):
    """
    Uniform browser-facing response when the Flask API is down.

    Returns:
        (body, status) tuple ready for JsonResponse.
    """
    logger.error('Flask API unavailable: %s', exc)
    return {
        'success': False,
        'message': (
            'Parking engine unavailable. Start the Flask API '
            '(python run_flask.py) and retry.'
        ),
    }, 503
