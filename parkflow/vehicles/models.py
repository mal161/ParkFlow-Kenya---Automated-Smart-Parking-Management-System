"""
Vehicles app models.

This module defines the Vehicle model for registration and tracking.
"""
from django.db import models
from django.core.validators import RegexValidator
from django.utils import timezone


class Vehicle(models.Model):
    """
    Represents a vehicle registered in the parking system.

    Attributes:
        registration_number: Unique vehicle registration plate (Kenyan format)
        vehicle_type: Type of vehicle for fee calculation
        created_at: Timestamp when the vehicle was first registered
        updated_at: Timestamp when the vehicle record was last updated
    """

    class VehicleType(models.TextChoices):
        CAR = 'CAR', 'Car'
        MOTORCYCLE = 'MOTORCYCLE', 'Motorcycle'
        TRUCK = 'TRUCK', 'Truck'
        BUS = 'BUS', 'Bus'
        OTHER = 'OTHER', 'Other'

    # Kenyan registration format: KXX 123X or KXX 123A etc.
    # This regex allows formats like: KAA 123A, KBZ 999Z, KCA 001A
    kenyan_plate_validator = RegexValidator(
        regex=r'^K[A-Z]{2}\s?\d{1,3}[A-Z]?$',
        message=(
            'Enter a valid Kenyan registration number '
            '(e.g., KAA 123A, KBZ 999Z, KCA 001A)'
        ),
        code='invalid_registration'
    )

    registration_number = models.CharField(
        max_length=12,
        unique=True,
        validators=[kenyan_plate_validator],
        help_text='Kenyan vehicle registration number (e.g., KAA 123A)'
    )
    vehicle_type = models.CharField(
        max_length=20,
        choices=VehicleType.choices,
        default=VehicleType.CAR
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'vehicles'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['registration_number']),
            models.Index(fields=['vehicle_type']),
        ]

    def __str__(self):
        return f"{self.registration_number} ({self.vehicle_type})"

    def save(self, *args, **kwargs):
        """Normalize registration number before saving."""
        # Remove spaces and convert to uppercase
        self.registration_number = self.registration_number.replace(' ', '').upper()
        # Insert space after 3rd character for display: KAA123A -> KAA 123A
        if len(self.registration_number) >= 4 and ' ' not in self.registration_number:
            self.registration_number = (
                self.registration_number[:3] + ' ' + self.registration_number[3:]
            )
        super().save(*args, **kwargs)

    def has_active_session(self) -> bool:
        """Check if this vehicle has an active parking session."""
        return self.parking_sessions.filter(
            status=ParkingSession.Status.ACTIVE
        ).exists()


# Import here to avoid circular import
from parking.models import ParkingSession  # noqa: E402