from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework.routers import DefaultRouter
from api.views import IngestionJobViewSet, EmissionsRecordViewSet, DashboardViewSet, login_view

router = DefaultRouter()
router.register(r"jobs", IngestionJobViewSet, basename="jobs")
router.register(r"records", EmissionsRecordViewSet, basename="records")
router.register(r"dashboard", DashboardViewSet, basename="dashboard")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/token/", login_view, name="api-token"),
    path("api/", include(router.urls)),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)