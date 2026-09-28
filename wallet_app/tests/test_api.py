import uuid
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from wallet_app.models import Tenant, Transaction, Wallet


def _is_postgres():
    """Check if the test database is PostgreSQL."""
    engine = settings.DATABASES["default"]["ENGINE"]
    return "postgresql" in engine


class BaseWalletTestCase(TestCase):
    """Base test case with common setup: creates a tenant, API client, and helper methods."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test Merchant")
        self.client = APIClient()
        self.client.defaults["HTTP_X_TENANT_ID"] = str(self.tenant.id)

    def create_wallet(self, name="John Doe", email="john@example.com", balance=0):
        """Helper to create a wallet and optionally seed it with a balance."""
        wallet = Wallet.objects.create(
            tenant=self.tenant, name=name, email=email, balance=balance
        )
        return wallet


# ═══════════════════════════════════════════════════════════════════════════════
# TENANT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TenantTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_create_tenant(self):
        """POST /api/tenants/ should create a new tenant with an auto-generated API key."""
        response = self.client.post("/api/tenants/", {"name": "Acme Corp"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["name"], "Acme Corp")
        self.assertIn("api_key", response.data)
        self.assertIn("id", response.data)

# ═══════════════════════════════════════════════════════════════════════════════
# WALLET TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class WalletTests(BaseWalletTestCase):
    def test_create_wallet(self):
        """POST /api/wallets/ should create a wallet under the current tenant."""
        response = self.client.post(
            "/api/wallets/",
            {"name": "Jane Doe", "email": "jane@example.com"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["name"], "Jane Doe")
        self.assertEqual(response.data["balance"], 0)

    def test_retrieve_wallet(self):
        """GET /api/wallets/{id}/ should return wallet details with balance."""
        wallet = self.create_wallet(balance=10000)
        response = self.client.get(f"/api/wallets/{wallet.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["balance"], 10000)

    def test_list_wallets(self):
        """GET /api/wallets/ should list only the current tenant's wallets."""
        self.create_wallet(name="Alice", email="alice@example.com")
        self.create_wallet(name="Bob", email="bob@example.com")

        # Create wallet under different tenant
        other_tenant = Tenant.objects.create(name="Other Tenant")
        Wallet.objects.create(
            tenant=other_tenant, name="Eve", email="eve@example.com"
        )

        response = self.client.get("/api/wallets/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)

    def test_duplicate_email_per_tenant_rejected(self):
        """Creating two wallets with the same email under one tenant should fail."""
        self.create_wallet(email="dup@example.com")
        response = self.client.post(
            "/api/wallets/",
            {"name": "Duplicate", "email": "dup@example.com"},
        )
        self.assertEqual(response.status_code, 400)


# ═══════════════════════════════════════════════════════════════════════════════
# DEPOSIT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class DepositTests(BaseWalletTestCase):
    def test_deposit_success(self):
        """Depositing funds should increase balance and create a ledger entry."""
        wallet = self.create_wallet()
        response = self.client.post(
            f"/api/wallets/{wallet.id}/deposit/",
            {"amount": 5000},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["type"], "DEPOSIT")
        self.assertEqual(response.data["amount"], 5000)
        self.assertEqual(response.data["balance_after"], 5000)

        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 5000)

    def test_deposit_invalid_amount(self):
        """Depositing zero or negative amount should be rejected."""
        wallet = self.create_wallet()
        response = self.client.post(
            f"/api/wallets/{wallet.id}/deposit/",
            {"amount": 0},
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            f"/api/wallets/{wallet.id}/deposit/",
            {"amount": -100},
        )
        self.assertEqual(response.status_code, 400)

    def test_deposit_nonexistent_wallet(self):
        """Depositing into a wallet that doesn't exist should return 404."""
        fake_id = uuid.uuid4()
        response = self.client.post(
            f"/api/wallets/{fake_id}/deposit/",
            {"amount": 1000},
        )
        self.assertEqual(response.status_code, 404)


# ═══════════════════════════════════════════════════════════════════════════════
# WITHDRAWAL TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class WithdrawTests(BaseWalletTestCase):
    def test_withdraw_success(self):
        """Withdrawing within balance should succeed."""
        wallet = self.create_wallet(balance=10000)
        response = self.client.post(
            f"/api/wallets/{wallet.id}/withdraw/",
            {"amount": 3000},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["balance_after"], 7000)

        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 7000)

    def test_withdraw_insufficient_funds(self):
        """Withdrawing more than balance should be rejected with 400."""
        wallet = self.create_wallet(balance=1000)
        response = self.client.post(
            f"/api/wallets/{wallet.id}/withdraw/",
            {"amount": 5000},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Insufficient funds", response.data["error"])

        # Balance should remain unchanged
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 1000)

    def test_withdraw_exact_balance(self):
        """Withdrawing the exact balance should succeed and leave zero."""
        wallet = self.create_wallet(balance=5000)
        response = self.client.post(
            f"/api/wallets/{wallet.id}/withdraw/",
            {"amount": 5000},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["balance_after"], 0)


# ═══════════════════════════════════════════════════════════════════════════════
# TRANSFER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TransferTests(BaseWalletTestCase):
    def test_transfer_success(self):
        """Transfer between same-tenant wallets should move funds atomically."""
        wallet_a = self.create_wallet(name="Alice", email="alice@test.com", balance=10000)
        wallet_b = self.create_wallet(name="Bob", email="bob@test.com", balance=0)

        response = self.client.post(
            "/api/transfers/",
            {
                "from_wallet_id": str(wallet_a.id),
                "to_wallet_id": str(wallet_b.id),
                "amount": 3000,
            },
        )
        self.assertEqual(response.status_code, 201)

        wallet_a.refresh_from_db()
        wallet_b.refresh_from_db()
        self.assertEqual(wallet_a.balance, 7000)
        self.assertEqual(wallet_b.balance, 3000)

        # Should create two paired transactions
        self.assertEqual(
            Transaction.objects.filter(wallet=wallet_a, type="TRANSFER_OUT").count(), 1
        )
        self.assertEqual(
            Transaction.objects.filter(wallet=wallet_b, type="TRANSFER_IN").count(), 1
        )

    def test_transfer_insufficient_funds(self):
        """Transfer more than sender's balance should be rejected."""
        wallet_a = self.create_wallet(name="Alice", email="alice@test.com", balance=1000)
        wallet_b = self.create_wallet(name="Bob", email="bob@test.com", balance=0)

        response = self.client.post(
            "/api/transfers/",
            {
                "from_wallet_id": str(wallet_a.id),
                "to_wallet_id": str(wallet_b.id),
                "amount": 5000,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Insufficient funds", response.data["error"])

        # Balances should remain unchanged
        wallet_a.refresh_from_db()
        wallet_b.refresh_from_db()
        self.assertEqual(wallet_a.balance, 1000)
        self.assertEqual(wallet_b.balance, 0)

    def test_transfer_same_wallet_rejected(self):
        """Transferring to the same wallet should be rejected."""
        wallet = self.create_wallet(balance=10000)
        response = self.client.post(
            "/api/transfers/",
            {
                "from_wallet_id": str(wallet.id),
                "to_wallet_id": str(wallet.id),
                "amount": 1000,
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_cross_tenant_transfer_rejected(self):
        """Transfer between wallets of different tenants must be rejected."""
        wallet_a = self.create_wallet(
            name="Alice", email="alice@test.com", balance=10000
        )

        # Create wallet under a different tenant
        other_tenant = Tenant.objects.create(name="Other Corp")
        wallet_b = Wallet.objects.create(
            tenant=other_tenant, name="Bob", email="bob@test.com", balance=0
        )

        response = self.client.post(
            "/api/transfers/",
            {
                "from_wallet_id": str(wallet_a.id),
                "to_wallet_id": str(wallet_b.id),
                "amount": 1000,
            },
        )
        self.assertEqual(response.status_code, 400)

        # Balances should remain unchanged
        wallet_a.refresh_from_db()
        wallet_b.refresh_from_db()
        self.assertEqual(wallet_a.balance, 10000)
        self.assertEqual(wallet_b.balance, 0)


# ═══════════════════════════════════════════════════════════════════════════════
# CROSS-TENANT ISOLATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class CrossTenantIsolationTests(TestCase):
    """Verify that a tenant cannot access another tenant's resources."""

    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Tenant A")
        self.tenant_b = Tenant.objects.create(name="Tenant B")

        self.wallet_a = Wallet.objects.create(
            tenant=self.tenant_a, name="Alice", email="alice@a.com", balance=5000
        )
        self.wallet_b = Wallet.objects.create(
            tenant=self.tenant_b, name="Bob", email="bob@b.com", balance=5000
        )

        self.client_a = APIClient()
        self.client_a.defaults["HTTP_X_TENANT_ID"] = str(self.tenant_a.id)

        self.client_b = APIClient()
        self.client_b.defaults["HTTP_X_TENANT_ID"] = str(self.tenant_b.id)

    def test_cannot_view_other_tenants_wallet(self):
        """Tenant A should not be able to retrieve Tenant B's wallet."""
        response = self.client_a.get(f"/api/wallets/{self.wallet_b.id}/")
        self.assertEqual(response.status_code, 404)

    def test_cannot_deposit_to_other_tenants_wallet(self):
        """Tenant A should not be able to deposit into Tenant B's wallet."""
        response = self.client_a.post(
            f"/api/wallets/{self.wallet_b.id}/deposit/",
            {"amount": 1000},
        )
        self.assertEqual(response.status_code, 404)

    def test_cannot_withdraw_from_other_tenants_wallet(self):
        """Tenant A should not be able to withdraw from Tenant B's wallet."""
        response = self.client_a.post(
            f"/api/wallets/{self.wallet_b.id}/withdraw/",
            {"amount": 1000},
        )
        self.assertEqual(response.status_code, 404)

    def test_cannot_view_other_tenants_transactions(self):
        """Tenant A should not be able to view Tenant B's transaction history."""
        response = self.client_a.get(
            f"/api/wallets/{self.wallet_b.id}/transactions/"
        )
        self.assertEqual(response.status_code, 404)

    def test_wallet_list_only_shows_own_wallets(self):
        """GET /api/wallets/ should only return the requesting tenant's wallets."""
        response_a = self.client_a.get("/api/wallets/")
        response_b = self.client_b.get("/api/wallets/")

        self.assertEqual(len(response_a.data), 1)
        self.assertEqual(response_a.data[0]["id"], str(self.wallet_a.id))

        self.assertEqual(len(response_b.data), 1)
        self.assertEqual(response_b.data[0]["id"], str(self.wallet_b.id))

    def test_missing_tenant_header_returns_401(self):
        """Requests without X-Tenant-ID or X-API-Key should get 401."""
        client = APIClient()  # No tenant header
        response = client.get("/api/wallets/")
        self.assertEqual(response.status_code, 401)

    def test_invalid_tenant_id_returns_403(self):
        """Requests with a nonexistent tenant ID should get 403."""
        client = APIClient()
        client.defaults["HTTP_X_TENANT_ID"] = str(uuid.uuid4())
        response = client.get("/api/wallets/")
        self.assertEqual(response.status_code, 403)

    def test_api_key_authentication(self):
        """Tenant should be resolvable via X-API-Key header."""
        client = APIClient()
        client.defaults["HTTP_X_API_KEY"] = str(self.tenant_a.api_key)
        response = client.get("/api/wallets/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)


# ═══════════════════════════════════════════════════════════════════════════════
# IDEMPOTENCY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class IdempotencyTests(BaseWalletTestCase):
    def test_deposit_idempotency(self):
        """Retrying a deposit with the same idempotency key should not double-charge."""
        wallet = self.create_wallet(balance=0)
        payload = {"amount": 5000, "idempotency_key": "dep-001"}

        # First request
        response1 = self.client.post(f"/api/wallets/{wallet.id}/deposit/", payload)
        self.assertEqual(response1.status_code, 201)

        # Retry with same key
        response2 = self.client.post(f"/api/wallets/{wallet.id}/deposit/", payload)
        # Should return cached response, not double-deposit
        self.assertIn(response2.status_code, [200, 201])

        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 5000)  # NOT 10000

    def test_withdraw_idempotency(self):
        """Retrying a withdrawal with the same idempotency key should not double-withdraw."""
        wallet = self.create_wallet(balance=10000)
        payload = {"amount": 3000, "idempotency_key": "wd-001"}

        response1 = self.client.post(f"/api/wallets/{wallet.id}/withdraw/", payload)
        self.assertEqual(response1.status_code, 201)

        response2 = self.client.post(f"/api/wallets/{wallet.id}/withdraw/", payload)
        self.assertIn(response2.status_code, [200, 201])

        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 7000)  # NOT 4000

    def test_transfer_idempotency(self):
        """Retrying a transfer with the same idempotency key should not double-transfer."""
        wallet_a = self.create_wallet(name="A", email="a@test.com", balance=10000)
        wallet_b = self.create_wallet(name="B", email="b@test.com", balance=0)

        payload = {
            "from_wallet_id": str(wallet_a.id),
            "to_wallet_id": str(wallet_b.id),
            "amount": 3000,
            "idempotency_key": "tx-001",
        }

        response1 = self.client.post("/api/transfers/", payload)
        self.assertEqual(response1.status_code, 201)

        response2 = self.client.post("/api/transfers/", payload)
        self.assertIn(response2.status_code, [200, 201])

        wallet_a.refresh_from_db()
        wallet_b.refresh_from_db()
        self.assertEqual(wallet_a.balance, 7000)  # NOT 4000
        self.assertEqual(wallet_b.balance, 3000)  # NOT 6000

    def test_same_idempotency_key_different_tenants(self):
        """Same idempotency key from different tenants should be independent."""
        other_tenant = Tenant.objects.create(name="Other Tenant")
        wallet1 = self.create_wallet(balance=10000)
        wallet2 = Wallet.objects.create(
            tenant=other_tenant, name="Other", email="other@test.com", balance=10000
        )

        payload = {"amount": 1000, "idempotency_key": "shared-key"}

        # Deposit under tenant 1
        response1 = self.client.post(
            f"/api/wallets/{wallet1.id}/deposit/", payload
        )
        self.assertEqual(response1.status_code, 201)

        # Deposit under tenant 2 with same key — should succeed (independent)
        client2 = APIClient()
        client2.defaults["HTTP_X_TENANT_ID"] = str(other_tenant.id)
        response2 = client2.post(
            f"/api/wallets/{wallet2.id}/deposit/", payload
        )
        self.assertEqual(response2.status_code, 201)


# ═══════════════════════════════════════════════════════════════════════════════
# LEDGER INTEGRITY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class LedgerTests(BaseWalletTestCase):
    def test_ledger_is_source_of_truth(self):
        """Sum of all ledger transactions should equal the wallet balance."""
        wallet = self.create_wallet(balance=0)

        # Perform several operations
        self.client.post(f"/api/wallets/{wallet.id}/deposit/", {"amount": 10000})
        self.client.post(f"/api/wallets/{wallet.id}/withdraw/", {"amount": 3000})
        self.client.post(f"/api/wallets/{wallet.id}/deposit/", {"amount": 2000})

        wallet.refresh_from_db()

        # Calculate expected balance from ledger
        txs = Transaction.objects.filter(wallet=wallet)
        ledger_balance = 0
        for tx in txs:
            if tx.type in ("DEPOSIT", "TRANSFER_IN"):
                ledger_balance += tx.amount
            elif tx.type in ("WITHDRAWAL", "TRANSFER_OUT"):
                ledger_balance -= tx.amount

        self.assertEqual(wallet.balance, ledger_balance)
        self.assertEqual(wallet.balance, 9000)

    def test_transactions_are_immutable(self):
        """Transaction count should match the number of operations performed."""
        wallet = self.create_wallet(balance=0)

        self.client.post(f"/api/wallets/{wallet.id}/deposit/", {"amount": 5000})
        self.client.post(f"/api/wallets/{wallet.id}/withdraw/", {"amount": 2000})

        txs = Transaction.objects.filter(wallet=wallet)
        self.assertEqual(txs.count(), 2)

    def test_transaction_history_paginated(self):
        """GET /api/wallets/{id}/transactions/ should return paginated results."""
        wallet = self.create_wallet(balance=0)

        # Create 25 deposits to exceed default page size of 20
        for i in range(25):
            self.client.post(f"/api/wallets/{wallet.id}/deposit/", {"amount": 100})

        response = self.client.get(f"/api/wallets/{wallet.id}/transactions/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("results", response.data)
        self.assertIn("count", response.data)
        self.assertEqual(response.data["count"], 25)
        self.assertEqual(len(response.data["results"]), 20)  # Default page size

        # Page 2
        response2 = self.client.get(
            f"/api/wallets/{wallet.id}/transactions/?page=2"
        )
        self.assertEqual(len(response2.data["results"]), 5)


# ═══════════════════════════════════════════════════════════════════════════════
# CONCURRENT TRANSFER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


import unittest


@unittest.skipUnless(
    _is_postgres(),
    "Concurrent tests require PostgreSQL — SQLite locks the entire table."
)
class ConcurrentTransferTests(TransactionTestCase):
    """
    Tests for race conditions using threads.
    Uses TransactionTestCase so each thread gets its own real DB transaction
    (TestCase wraps everything in a transaction, defeating select_for_update).
    Requires PostgreSQL for row-level locking via select_for_update.
    """

    def test_concurrent_withdrawals_no_overdraft(self):
        """
        Two simultaneous withdrawals should not both succeed if the
        combined amount exceeds the balance.
        """
        tenant = Tenant.objects.create(name="Race Test Tenant")
        wallet = Wallet.objects.create(
            tenant=tenant, name="Racer", email="racer@test.com", balance=5000
        )

        results = []

        def do_withdraw():
            client = APIClient()
            client.defaults["HTTP_X_TENANT_ID"] = str(tenant.id)
            resp = client.post(
                f"/api/wallets/{wallet.id}/withdraw/",
                {"amount": 3000},
            )
            results.append(resp.status_code)

        with ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(do_withdraw)
            f2 = executor.submit(do_withdraw)
            f1.result()
            f2.result()

        wallet.refresh_from_db()

        # One should succeed (201), one should fail (400 insufficient funds)
        self.assertIn(201, results)
        self.assertIn(400, results)
        self.assertEqual(wallet.balance, 2000)

    def test_concurrent_transfers_preserve_total(self):
        """
        Concurrent transfers between wallets should preserve the total balance.
        """
        tenant = Tenant.objects.create(name="Concurrent Tenant")
        wallet_a = Wallet.objects.create(
            tenant=tenant, name="A", email="a@test.com", balance=10000
        )
        wallet_b = Wallet.objects.create(
            tenant=tenant, name="B", email="b@test.com", balance=10000
        )

        def transfer_a_to_b():
            client = APIClient()
            client.defaults["HTTP_X_TENANT_ID"] = str(tenant.id)
            client.post(
                "/api/transfers/",
                {
                    "from_wallet_id": str(wallet_a.id),
                    "to_wallet_id": str(wallet_b.id),
                    "amount": 1000,
                },
            )

        def transfer_b_to_a():
            client = APIClient()
            client.defaults["HTTP_X_TENANT_ID"] = str(tenant.id)
            client.post(
                "/api/transfers/",
                {
                    "from_wallet_id": str(wallet_b.id),
                    "to_wallet_id": str(wallet_a.id),
                    "amount": 2000,
                },
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(transfer_a_to_b)
            f2 = executor.submit(transfer_b_to_a)
            f1.result()
            f2.result()

        wallet_a.refresh_from_db()
        wallet_b.refresh_from_db()

        # Total balance should always be preserved (20000)
        self.assertEqual(wallet_a.balance + wallet_b.balance, 20000)
