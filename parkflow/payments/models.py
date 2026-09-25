"""
Payments app models.

This module defines the Payment model for tracking parking fee payments.
"""
from django.db import models
from django.utils import timezone


class Payment(models.Model):
    """
    Represents a payment for a parking session.

    Attributes:
        session: Foreign key to the ParkingSession
        amount: Amount paid in KSh
        payment_method: Method of payment (CASH only for now; other methods later)
        payment_status: Status of the payment (PENDING, PAID, FAILED, REFUNDED)
        transaction_reference: Unique transaction identifier
        payment_time: When the payment was processed
        created_at: Timestamp when the payment record was created
        updated_at: Timestamp when the payment record was last updated
    """

    class PaymentMethod(models.TextChoices):
        # Cash is the only enabled method for now; other methods
        # (M-Pesa, card) will be configured later.
        CASH = 'CASH', 'Cash'
        MPESA = 'MPESA', 'M-Pesa (not enabled yet)'
        CARD = 'CARD', 'Card (not enabled yet)'

    class PaymentStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        PAID = 'PAID', 'Paid'
        FAILED = 'FAILED', 'Failed'
        REFUNDED = 'REFUNDED', 'Refunded'

    session = models.ForeignKey(
        'parking.ParkingSession',
        on_delete=models.PROTECT,
        related_name='payments'
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CASH
    )
    payment_status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING
    )
    transaction_reference = models.CharField(max_length=100, unique=True)
    payment_time = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'payments'
        ordering = ['-payment_time']
        indexes = [
            models.Index(fields=['payment_status']),
            models.Index(fields=['transaction_reference']),
            models.Index(fields=['session', 'payment_status']),
        ]

    def __str__(self):
        return f"Payment {self.transaction_reference}: KSh {self.amount} ({self.payment_status})"

    def is_successful(self) -> bool:
        """Check if payment was successful."""
        return self.payment_status == self.PaymentStatus.PAID

    def mark_paid(self):
        """Mark payment as paid."""
        self.payment_status = self.PaymentStatus.PAID
        self.payment_time = timezone.now()
        self.save(update_fields=['payment_status', 'payment_time', 'updated_at'])