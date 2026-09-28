from rest_framework import serializers

from wallet_app.models import Tenant, Transaction, Wallet


#  Tenant

class TenantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = ["id", "name", "api_key", "created_at"]
        read_only_fields = ["id", "api_key", "created_at"]


#  Wallet

class WalletCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wallet
        fields = ["id", "name", "email", "balance", "created_at"]
        read_only_fields = ["id", "balance", "created_at"]


class WalletDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wallet
        fields = ["id", "tenant", "name", "email", "balance", "created_at", "updated_at"]
        read_only_fields = fields


#  Transaction

class TransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transaction
        fields = [
            "id", "wallet", "type", "amount",
            "balance_after", "reference_tx", "idempotency_key", "created_at",
        ]
        read_only_fields = fields


#  Action Serializers

class MoneyActionSerializer(serializers.Serializer):
    amount = serializers.IntegerField(min_value=1, help_text="Amount in minor units (must be positive)")
    idempotency_key = serializers.CharField(
        max_length=255, required=False, allow_blank=False,
        help_text="Client-provided key to prevent duplicate operations",
    )


class TransferSerializer(serializers.Serializer):
    from_wallet_id = serializers.UUIDField(help_text="Source wallet UUID")
    to_wallet_id = serializers.UUIDField(help_text="Destination wallet UUID")
    amount = serializers.IntegerField(min_value=1, help_text="Amount in minor units (must be positive)")
    idempotency_key = serializers.CharField(
        max_length=255, required=False, allow_blank=False,
        help_text="Client-provided key to prevent duplicate transfers",
    )
