from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin

from wallet_app.models import Tenant


# Endpoints that don't require tenant resolution
TENANT_EXEMPT_PATHS = [
    "/api/tenants/",
]


class TenantMiddleware(MiddlewareMixin):
    def process_request(self, request):
        for exempt_path in TENANT_EXEMPT_PATHS:
            if request.path.startswith(exempt_path):
                return None

        tenant_id = request.headers.get("X-Tenant-ID")
        api_key = request.headers.get("X-API-Key")

        if not tenant_id and not api_key:
            return JsonResponse(
                {"error": "Missing tenant identification. Provide X-Tenant-ID or X-API-Key header."},
                status=401,
            )

        try:
            if tenant_id:
                tenant = Tenant.objects.get(id=tenant_id)
            else:
                tenant = Tenant.objects.get(api_key=api_key)
        except (Tenant.DoesNotExist, ValueError):
            return JsonResponse(
                {"error": "Invalid tenant credentials."},
                status=403,
            )

        request.tenant = tenant
        return None
