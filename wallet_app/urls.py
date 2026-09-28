from django.urls import path, include
from rest_framework.routers import DefaultRouter

from wallet_app.views import TenantViewSet, TransferViewSet, WalletViewSet

router = DefaultRouter()
router.register(r"tenants", TenantViewSet, basename="tenant")
router.register(r"wallets", WalletViewSet, basename="wallet")
router.register(r"transfers", TransferViewSet, basename="transfer")

urlpatterns = [
    path("", include(router.urls)),
]
