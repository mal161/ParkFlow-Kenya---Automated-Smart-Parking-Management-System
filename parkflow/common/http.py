"""
Small helpers for the JSON proxy views.

They turn malformed input (non-JSON bodies, JSON arrays, non-string
fields) into clean 400 responses instead of AttributeError/500s.
"""
import json

from django.http import JsonResponse


class BadInput(Exception):
    """Raised by the helpers below; carries a client-facing message."""


def error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({'success': False, 'message': message}, status=status)


def json_body(request) -> dict:
    """The request body as a JSON object."""
    try:
        data = json.loads(request.body or b'{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise BadInput('Invalid JSON data')
    if not isinstance(data, dict):
        raise BadInput('Invalid JSON data: an object is required')
    return data


def text_field(data: dict, name: str, *, required: bool = True,
               default: str = '', max_length: int = 100) -> str:
    """A stripped string field (missing -> default, or error if required)."""
    value = data.get(name, default)
    if value is None:
        value = default
    if not isinstance(value, str):
        raise BadInput(f'{name} must be a string')
    value = value.strip()
    if required and not value:
        raise BadInput(f'{name.replace("_", " ").capitalize()} is required')
    if len(value) > max_length:
        raise BadInput(f'{name} is too long')
    return value
