"""
Flask API Configuration for ParkFlow Kenya.

Centralized configuration management for the Flask API service.
"""
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class Config:
    """Base configuration class."""

    # Flask
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')
    DEBUG = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'

    # Supabase
    SUPABASE_URL = os.environ.get('SUPABASE_URL')
    SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
    SUPABASE_SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_KEY')

    # API
    API_PREFIX = '/api/v1'

    # Parking Business Rules
    DEFAULT_FREE_MINUTES = 30
    DEFAULT_RATES = {
        'CAR': {
            'free_minutes': 30,
            'two_hour_rate': 50,
            'four_hour_rate': 100,
            'six_hour_rate': 300,
            'over_six_hour_rate': 500,
        },
        'MOTORCYCLE': {
            'free_minutes': 30,
            'two_hour_rate': 20,
            'four_hour_rate': 50,
            'six_hour_rate': 100,
            'over_six_hour_rate': 200,
        },
        'TRUCK': {
            'free_minutes': 30,
            'two_hour_rate': 100,
            'four_hour_rate': 200,
            'six_hour_rate': 500,
            'over_six_hour_rate': 1000,
        },
        'BUS': {
            'free_minutes': 30,
            'two_hour_rate': 150,
            'four_hour_rate': 300,
            'six_hour_rate': 800,
            'over_six_hour_rate': 1500,
        },
        'OTHER': {
            'free_minutes': 30,
            'two_hour_rate': 50,
            'four_hour_rate': 100,
            'six_hour_rate': 300,
            'over_six_hour_rate': 500,
        },
    }

    # Slot Statuses
    SLOT_STATUSES = ['AVAILABLE', 'OCCUPIED', 'MAINTENANCE', 'RESERVED', 'OUT_OF_SERVICE']

    # Session Statuses
    SESSION_STATUSES = ['ACTIVE', 'COMPLETED', 'CANCELLED']

    # Payment Statuses
    PAYMENT_STATUSES = ['PENDING', 'PAID', 'FAILED', 'REFUNDED']

    # Payment Methods
    # Only CASH is enabled for now; other methods will be configured later.
    PAYMENT_METHODS = ['CASH']

    # Timezone
    TIMEZONE = 'Africa/Nairobi'


class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True


class ProductionConfig(Config):
    """Production configuration."""
    DEBUG = False


class TestingConfig(Config):
    """Testing configuration."""
    TESTING = True
    DEBUG = True


# Configuration dictionary
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig,
}