"""
Parking app models.

This module defines the core data models for the parking management system.
Models are designed to work with Supabase PostgreSQL as the persistent database.
"""
from django.db import models
from django.utils import timezone


class ParkingSlot(models.Model):
    """
    Represents a physical parking slot in the facility.

    Attributes:
        slot_number: Unique identifier for the slot (e.g., 'A01', 'B12')
        status: Current state of the slot (AVAILABLE, OCCUPIED, MAINTENANCE, RESERVED)
        location: Physical location description (e.g., 'Block A', 'Level 1')
        created_at: Timestamp when the slot was created
        updated_at: Timestamp when the slot was last updated
    """

    class Status(models.TextChoices):
        AVAILABLE = 'AVAILABLE', 'Available'
        OCCUPIED = 'OCCUPIED', 'Occupied'
        MAINTENANCE = 'MAINTENANCE', 'Maintenance'
        RESERVED = 'RESERVED', 'Reserved'
        OUT_OF_SERVICE = 'OUT_OF_SERVICE', 'Out of Service'

    slot_number = models.CharField(max_length=10, unique=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.AVAILABLE
    )
    location = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'parking_slots'
        ordering = ['slot_number']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['slot_number']),
        ]

    def __str__(self):
        return f"Slot {self.slot_number} ({self.status})"

    def is_available(self) -> bool:
        """Check if the slot is available for parking."""
        return self.status == self.Status.AVAILABLE


class ParkingSession(models.Model):
    """
    Represents an active or completed parking session.

    A session links a vehicle to a parking slot with entry/exit times
    and calculates fees.

    Attributes:
        vehicle: Foreign key to the Vehicle model
        slot: Foreign key to the ParkingSlot model
        entry_time: When the vehicle entered the parking facility
        exit_time: When the vehicle exited (None if still parked)
        duration_minutes: Total parking duration in minutes
        amount_due: Calculated parking fee in KSh
        status: Session status (ACTIVE, COMPLETED, CANCELLED)
        created_at: Timestamp when the session was created
        updated_at: Timestamp when the session was last updated
    """

    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        COMPLETED = 'COMPLETED', 'Completed'
        CANCELLED = 'CANCELLED', 'Cancelled'

    vehicle = models.ForeignKey(
        'vehicles.Vehicle',
        on_delete=models.PROTECT,
        related_name='parking_sessions'
    )
    slot = models.ForeignKey(
        ParkingSlot,
        on_delete=models.PROTECT,
        related_name='parking_sessions'
    )
    entry_time = models.DateTimeField(default=timezone.now)
    exit_time = models.DateTimeField(null=True, blank=True)
    duration_minutes = models.PositiveIntegerField(default=0)
    amount_due = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'parking_sessions'
        ordering = ['-entry_time']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['vehicle', 'status']),
            models.Index(fields=['entry_time']),
        ]

    def __str__(self):
        return f"Session {self.id}: {self.vehicle} @ {self.slot}"

    @property
    def is_active(self) -> bool:
        """Check if the session is currently active."""
        return self.status == self.Status.ACTIVE

    def calculate_duration(self) -> int:
        """
        Calculate parking duration in minutes.

        Returns:
            Duration in minutes (rounded up to nearest minute)
        """
        if self.exit_time:
            delta = self.exit_time - self.entry_time
        else:
            delta = timezone.now() - self.entry_time

        # Round UP to the next whole minute so that fee boundaries are
        # evaluated consistently: 30:01 parked -> 31 minutes -> fee applies.
        total_seconds = delta.total_seconds()
        minutes = int(total_seconds // 60)
        if total_seconds % 60 > 0:
            minutes += 1
        return max(1, minutes)


class ParkingRate(models.Model):
    """
    Configurable parking fee rates by vehicle type.

    This model allows dynamic fee configuration without code changes.
    Only one active rate set per vehicle type at a time.

    Attributes:
        vehicle_type: Type of vehicle (car, motorcycle, truck, etc.)
        free_minutes: Free parking period in minutes
        two_hour_rate: Fee for parking up to 2 hours
        four_hour_rate: Fee for parking up to 4 hours
        six_hour_rate: Fee for parking up to 6 hours
        over_six_hour_rate: Fee for parking over 6 hours
        active: Whether this rate is currently active
        effective_from: Date from which this rate is effective
        created_at: Timestamp when the rate was created
        updated_at: Timestamp when the rate was last updated
    """

    class VehicleType(models.TextChoices):
        CAR = 'CAR', 'Car'
        MOTORCYCLE = 'MOTORCYCLE', 'Motorcycle'
        TRUCK = 'TRUCK', 'Truck'
        BUS = 'BUS', 'Bus'
        OTHER = 'OTHER', 'Other'

    vehicle_type = models.CharField(
        max_length=20,
        choices=VehicleType.choices,
        default=VehicleType.CAR
    )
    free_minutes = models.PositiveIntegerField(default=30)
    two_hour_rate = models.DecimalField(max_digits=10, decimal_places=2, default=50)
    four_hour_rate = models.DecimalField(max_digits=10, decimal_places=2, default=100)
    six_hour_rate = models.DecimalField(max_digits=10, decimal_places=2, default=300)
    over_six_hour_rate = models.DecimalField(max_digits=10, decimal_places=2, default=500)
    active = models.BooleanField(default=True)
    effective_from = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'parking_rates'
        ordering = ['-effective_from']
        constraints = [
            models.UniqueConstraint(
                fields=['vehicle_type', 'active'],
                condition=models.Q(active=True),
                name='unique_active_rate_per_vehicle_type'
            )
        ]

    def __str__(self):
        return f"Rate for {self.vehicle_type} (KSh {self.two_hour_rate}/2hr)"

    def calculate_fee(self, duration_minutes: int) -> int:
        """
        Calculate parking fee based on duration.

        Fee structure:
        - 0-30 min: Free
        - 31-120 min: KSh 50
        - 121-240 min: KSh 100
        - 241-360 min: KSh 300
        - 361+ min: KSh 500

        Args:
            duration_minutes: Parking duration in minutes

        Returns:
            Fee in KSh (integer)
        """
        if duration_minutes <= self.free_minutes:
            return 0
        elif duration_minutes <= 120:
            return int(self.two_hour_rate)
        elif duration_minutes <= 240:
            return int(self.four_hour_rate)
        elif duration_minutes <= 360:
            return int(self.six_hour_rate)
        else:
            return int(self.over_six_hour_rate)