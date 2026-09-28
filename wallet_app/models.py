import uuid

from django.db import models


class Tenant(models.Model):
    """
    Represents a merchant or organization on the platform.
    Each tenant has an isolated namespace — their wallets, transactions,
    and idempotency keys are invisible to other tenants.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    api_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Wallet(models.Model):
    """
    A wallet belongs to a tenant and represents a single user/account.
    The 'balance' field is a denormalized cache — the Transaction ledger
    is the authoritative source of truth.
    Money is stored as integer minor units (paisa / cents).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="wallets"
    )
    name = models.CharField(max_length=255, help_text="Wallet holder name")
    email = models.EmailField(help_text="Wallet holder email")
    balance = models.BigIntegerField(
        default=0, help_text="Balance in minor units (paisa/cents)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Unique email per tenant — same email can exist across tenants
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "email"], name="unique_email_per_tenant"
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.tenant.name})"


class Transaction(models.Model):
    """
    Immutable ledger entry. Every balance change creates a Transaction.
    This is the source of truth — wallet.balance is just a cached total.
    Transactions are never updated or deleted.
    """

    class TransactionType(models.TextChoices):
        DEPOSIT = "DEPOSIT", "Deposit"
        WITHDRAWAL = "WITHDRAWAL", "Withdrawal"
        TRANSFER_IN = "TRANSFER_IN", "Transfer In"
        TRANSFER_OUT = "TRANSFER_OUT", "Transfer Out"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(
        Wallet, on_delete=models.CASCADE, related_name="transactions"
    )
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="transactions"
    )
    type = models.CharField(max_length=20, choices=TransactionType.choices)
    amount = models.BigIntegerField(
        help_text="Amount in minor units (always positive)"
    )
    balance_after = models.BigIntegerField(
        help_text="Wallet balance after this transaction"
    )
    reference_tx = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="paired_tx",
        help_text="Links the paired leg of a transfer",
    )
    idempotency_key = models.CharField(
        max_length=255, null=True, blank=True,
        help_text="Client-provided key to prevent duplicate operations",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["wallet", "-created_at"]),
            models.Index(fields=["tenant", "idempotency_key"]),
        ]

    def __str__(self):
        return f"{self.type} {self.amount} on {self.wallet}"


class IdempotencyKey(models.Model):
    """
    Stores client-provided idempotency keys scoped per tenant.
    When a duplicate key is received, the cached response is returned
    instead of processing the request again.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="idempotency_keys"
    )
    key = models.CharField(max_length=255)
    response_status = models.IntegerField(
        help_text="HTTP status code of the original response"
    )
    response_body = models.JSONField(
        help_text="Cached JSON response body"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "key"], name="unique_idempotency_key_per_tenant"
            )
        ]

    def __str__(self):
        return f"{self.key} ({self.tenant.name})"
