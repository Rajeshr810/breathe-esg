from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import TemplateView
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.routers import DefaultRouter

from api.views import IngestionJobViewSet, EmissionsRecordViewSet, DashboardViewSet

router = DefaultRouter()
router.register(r"jobs", IngestionJobViewSet, basename="jobs")
router.register(r"records", EmissionsRecordViewSet, basename="records")
router.register(r"dashboard", DashboardViewSet, basename="dashboard")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/token/", obtain_auth_token, name="api-token"),
    path("api/", include(router.urls)),
    # Serve React frontend for all non-API routes
    re_path(r"^(?!api/|admin/|media/).*$",
            TemplateView.as_view(template_name="index.html"),
            name="frontend"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
