from django.db import transaction

from wallet_app.models import IdempotencyKey, Transaction, Wallet


class InsufficientFundsError(Exception):
    """Raised when a wallet has insufficient balance for withdrawal/transfer."""
    pass


class CrossTenantTransferError(Exception):
    """Raised when a transfer is attempted between wallets of different tenants."""
    pass


class DuplicateIdempotencyKeyError(Exception):
    """Raised when an idempotency key has already been used."""

    def __init__(self, cached_response, cached_status):
        self.cached_response = cached_response
        self.cached_status = cached_status
        super().__init__("Duplicate idempotency key")


def claim_idempotency(tenant, idempotency_key):
    """Claim a key in the current transaction, or replay its cached response."""
    if not idempotency_key:
        return None

    record, created = IdempotencyKey.objects.get_or_create(
        tenant=tenant,
        key=idempotency_key,
        defaults={"response_status": 0, "response_body": {}},
    )
    if not created:
        raise DuplicateIdempotencyKeyError(
            cached_response=record.response_body,
            cached_status=record.response_status,
        )
    return record


def save_idempotency(record, response_status, response_body):
    """Save the completed response on the key claimed by this transaction."""
    if record is None:
        return

    record.response_status = response_status
    record.response_body = response_body
    record.save(update_fields=["response_status", "response_body"])


def deposit(tenant, wallet_id, amount, idempotency_key=None):
    """
    Deposit funds into a wallet.

    Uses select_for_update to lock the wallet row during the transaction.
    Records an immutable ledger entry. Idempotent via idempotency_key.
    """
    with transaction.atomic():
        idempotency_record = claim_idempotency(tenant, idempotency_key)
        try:
            wallet = (
                Wallet.objects.select_for_update()
                .get(id=wallet_id, tenant=tenant)
            )
        except Wallet.DoesNotExist:
            raise Wallet.DoesNotExist("Wallet not found or does not belong to this tenant.")

        wallet.balance += amount
        wallet.save(update_fields=["balance", "updated_at"])

        tx = Transaction.objects.create(
            wallet=wallet,
            tenant=tenant,
            type=Transaction.TransactionType.DEPOSIT,
            amount=amount,
            balance_after=wallet.balance,
            idempotency_key=idempotency_key,
        )

        response_body = {
            "transaction_id": str(tx.id),
            "wallet_id": str(wallet.id),
            "type": tx.type,
            "amount": tx.amount,
            "balance_after": tx.balance_after,
        }

        save_idempotency(idempotency_record, 201, response_body)

    return tx, wallet


def withdraw(tenant, wallet_id, amount, idempotency_key=None):
    """
    Withdraw funds from a wallet.

    Rejects if balance is insufficient. Uses select_for_update for row locking.
    Records an immutable ledger entry. Idempotent via idempotency_key.
    """
    with transaction.atomic():
        idempotency_record = claim_idempotency(tenant, idempotency_key)
        try:
            wallet = (
                Wallet.objects.select_for_update()
                .get(id=wallet_id, tenant=tenant)
            )
        except Wallet.DoesNotExist:
            raise Wallet.DoesNotExist("Wallet not found or does not belong to this tenant.")

        if wallet.balance < amount:
            raise InsufficientFundsError(
                f"Insufficient funds. Available: {wallet.balance}, Requested: {amount}"
            )

        wallet.balance -= amount
        wallet.save(update_fields=["balance", "updated_at"])

        tx = Transaction.objects.create(
            wallet=wallet,
            tenant=tenant,
            type=Transaction.TransactionType.WITHDRAWAL,
            amount=amount,
            balance_after=wallet.balance,
            idempotency_key=idempotency_key,
        )

        response_body = {
            "transaction_id": str(tx.id),
            "wallet_id": str(wallet.id),
            "type": tx.type,
            "amount": tx.amount,
            "balance_after": tx.balance_after,
        }

        save_idempotency(idempotency_record, 201, response_body)

    return tx, wallet


def transfer(tenant, from_wallet_id, to_wallet_id, amount, idempotency_key=None):
    if str(from_wallet_id) == str(to_wallet_id):
        raise ValueError("Cannot transfer to the same wallet.")

    with transaction.atomic():
        idempotency_record = claim_idempotency(tenant, idempotency_key)
        # Lock wallets in consistent order (ascending ID) to prevent deadlocks
        ordered_ids = sorted([str(from_wallet_id), str(to_wallet_id)])

        try:
            first_wallet = (
                Wallet.objects.select_for_update()
                .get(id=ordered_ids[0], tenant=tenant)
            )
            second_wallet = (
                Wallet.objects.select_for_update()
                .get(id=ordered_ids[1], tenant=tenant)
            )
        except Wallet.DoesNotExist:
            raise CrossTenantTransferError(
                "Both wallets must belong to the same tenant."
            )

        # Map wallets back to from/to
        wallet_map = {str(w.id): w for w in [first_wallet, second_wallet]}
        from_wallet = wallet_map[str(from_wallet_id)]
        to_wallet = wallet_map[str(to_wallet_id)]

        # Check sufficient funds
        if from_wallet.balance < amount:
            raise InsufficientFundsError(
                f"Insufficient funds. Available: {from_wallet.balance}, Requested: {amount}"
            )

        # Execute transfer
        from_wallet.balance -= amount
        from_wallet.save(update_fields=["balance", "updated_at"])

        to_wallet.balance += amount
        to_wallet.save(update_fields=["balance", "updated_at"])

        # Create paired ledger entries
        tx_out = Transaction.objects.create(
            wallet=from_wallet,
            tenant=tenant,
            type=Transaction.TransactionType.TRANSFER_OUT,
            amount=amount,
            balance_after=from_wallet.balance,
            idempotency_key=idempotency_key,
        )

        tx_in = Transaction.objects.create(
            wallet=to_wallet,
            tenant=tenant,
            type=Transaction.TransactionType.TRANSFER_IN,
            amount=amount,
            balance_after=to_wallet.balance,
            idempotency_key=idempotency_key,
        )

        # Link the paired transaction legs
        tx_out.reference_tx = tx_in
        tx_out.save(update_fields=["reference_tx"])
        tx_in.reference_tx = tx_out
        tx_in.save(update_fields=["reference_tx"])

        response_body = {
            "transfer_out": {
                "transaction_id": str(tx_out.id),
                "wallet_id": str(from_wallet.id),
                "type": tx_out.type,
                "amount": tx_out.amount,
                "balance_after": tx_out.balance_after,
            },
            "transfer_in": {
                "transaction_id": str(tx_in.id),
                "wallet_id": str(to_wallet.id),
                "type": tx_in.type,
                "amount": tx_in.amount,
                "balance_after": tx_in.balance_after,
            },
        }

        save_idempotency(idempotency_record, 201, response_body)

    return tx_out, tx_in, from_wallet, to_wallet
