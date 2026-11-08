"""
Flask API configuration for ParkFlow Kenya.

Business rules (fee tiers, vehicle types, statuses) live in the
algorithms / data_structures modules - the single source of truth -
so they are intentionally not duplicated here.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _env_flag(name: str, default: str = 'false') -> bool:
    return os.environ.get(name, default).strip().lower() in ('1', 'true', 'yes')


class Config:
    """Base configuration."""

    DEBUG = False
    TESTING = False

    # Supabase (read by SupabaseService; listed here for discoverability)
    SUPABASE_URL = os.environ.get('SUPABASE_URL')

    API_PREFIX = '/api/v1'

    # Shared secret Django sends as X-API-Key. Empty = no check (local dev).
    API_KEY = os.environ.get('FLASK_API_KEY', '')

    # Let clients supply exit_time on /parking/exit. Only for automated
    # end-to-end tests - in normal operation it would allow back-dating
    # an exit to avoid fees.
    ALLOW_EXIT_TIME_OVERRIDE = _env_flag('PARKFLOW_ALLOW_EXIT_TIME_OVERRIDE')


class DevelopmentConfig(Config):
    DEBUG = _env_flag('FLASK_DEBUG')


class ProductionConfig(Config):
    pass


class TestingConfig(Config):
    TESTING = True
    ALLOW_EXIT_TIME_OVERRIDE = True


_CONFIGS = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig,
}


def config_by_name(name: str):
    """Configuration class for FLASK_ENV (falls back to development)."""
    return _CONFIGS.get((name or 'default').lower(), DevelopmentConfig)
