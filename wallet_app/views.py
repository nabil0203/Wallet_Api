from django.db import IntegrityError
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.mixins import CreateModelMixin, ListModelMixin, RetrieveModelMixin
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from wallet_app import services
from wallet_app.models import Tenant, Transaction, Wallet
from wallet_app.serializers import (
    MoneyActionSerializer,
    TenantSerializer,
    TransactionSerializer,
    TransferSerializer,
    WalletCreateSerializer,
    WalletDetailSerializer,
)


# ── Pagination ───────────────────────────────────────────────────────────────


class TransactionPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


# ── Tenant ViewSet ───────────────────────────────────────────────────────────


class TenantViewSet(CreateModelMixin, GenericViewSet):
    """
    Create tenants.
    Tenant creation does not require X-Tenant-ID (exempt in middleware).
    """

    queryset = Tenant.objects.all()
    serializer_class = TenantSerializer


# ── Wallet ViewSet ───────────────────────────────────────────────────────────


class WalletViewSet(
    CreateModelMixin, RetrieveModelMixin, ListModelMixin, GenericViewSet
):
    """
    CRUD for wallets scoped to the current tenant.

    Endpoints:
      POST   /api/wallets/                    → Create wallet
      GET    /api/wallets/                    → List tenant's wallets
      GET    /api/wallets/{id}/               → Wallet detail + balance
      POST   /api/wallets/{id}/deposit/       → Deposit funds
      POST   /api/wallets/{id}/withdraw/      → Withdraw funds
      GET    /api/wallets/{id}/transactions/  → Paginated transaction history
    """

    def get_queryset(self):
        """Filter wallets to current tenant only — enforces tenant isolation."""
        return Wallet.objects.filter(tenant=self.request.tenant)

    def get_serializer_class(self):
        if self.action == "create":
            return WalletCreateSerializer
        return WalletDetailSerializer

    def perform_create(self, serializer):
        """Attach the tenant from the request to the new wallet."""
        serializer.save(tenant=self.request.tenant)

    def create(self, request, *args, **kwargs):
        """Override to handle duplicate email within a tenant gracefully."""
        try:
            return super().create(request, *args, **kwargs)
        except IntegrityError:
            return Response(
                {"error": "A wallet with this email already exists for this tenant."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    # ── Deposit ──────────────────────────────────────────────────────────

    @action(detail=True, methods=["post"], url_path="deposit")
    def deposit(self, request, pk=None):
        """Deposit funds into a wallet. Idempotent via idempotency_key."""
        serializer = MoneyActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            tx, _wallet = services.deposit(
                tenant=request.tenant,
                wallet_id=pk,
                amount=serializer.validated_data["amount"],
                idempotency_key=serializer.validated_data.get("idempotency_key"),
            )
        except Wallet.DoesNotExist:
            return Response(
                {"error": "Wallet not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except services.DuplicateIdempotencyKeyError as e:
            return Response(e.cached_response, status=e.cached_status)

        return Response(
            TransactionSerializer(tx).data,
            status=status.HTTP_201_CREATED,
        )

    # ── Withdraw ─────────────────────────────────────────────────────────

    @action(detail=True, methods=["post"], url_path="withdraw")
    def withdraw(self, request, pk=None):
        """Withdraw funds from a wallet. Rejects if balance is insufficient."""
        serializer = MoneyActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            tx, _wallet = services.withdraw(
                tenant=request.tenant,
                wallet_id=pk,
                amount=serializer.validated_data["amount"],
                idempotency_key=serializer.validated_data.get("idempotency_key"),
            )
        except Wallet.DoesNotExist:
            return Response(
                {"error": "Wallet not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except services.InsufficientFundsError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except services.DuplicateIdempotencyKeyError as e:
            return Response(e.cached_response, status=e.cached_status)

        return Response(
            TransactionSerializer(tx).data,
            status=status.HTTP_201_CREATED,
        )

    # ── Transaction History ──────────────────────────────────────────────

    @action(detail=True, methods=["get"], url_path="transactions")
    def transactions(self, request, pk=None):
        """Paginated transaction history for a wallet."""
        # Verify wallet belongs to tenant
        try:
            wallet = Wallet.objects.get(id=pk, tenant=request.tenant)
        except Wallet.DoesNotExist:
            return Response(
                {"error": "Wallet not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        txs = Transaction.objects.filter(wallet=wallet, tenant=request.tenant)
        paginator = TransactionPagination()
        page = paginator.paginate_queryset(txs, request)
        serializer = TransactionSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


# ── Transfer ViewSet ─────────────────────────────────────────────────────────


class TransferViewSet(CreateModelMixin, GenericViewSet):
    """
    POST /api/transfers/ → Transfer funds between two wallets of the same tenant.

    Atomic operation: both sides succeed or neither does.
    Rejects cross-tenant transfers.
    """

    serializer_class = TransferSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            tx_out, tx_in, _from_wallet, _to_wallet = services.transfer(
                tenant=request.tenant,
                from_wallet_id=serializer.validated_data["from_wallet_id"],
                to_wallet_id=serializer.validated_data["to_wallet_id"],
                amount=serializer.validated_data["amount"],
                idempotency_key=serializer.validated_data.get("idempotency_key"),
            )
        except Wallet.DoesNotExist:
            return Response(
                {"error": "One or both wallets not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except services.CrossTenantTransferError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except services.InsufficientFundsError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ValueError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except services.DuplicateIdempotencyKeyError as e:
            return Response(e.cached_response, status=e.cached_status)

        return Response(
            {
                "transfer_out": TransactionSerializer(tx_out).data,
                "transfer_in": TransactionSerializer(tx_in).data,
            },
            status=status.HTTP_201_CREATED,
        )
