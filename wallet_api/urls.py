from django.urls import include, path

urlpatterns = [
    path("api/", include("wallet_app.urls")),
]
