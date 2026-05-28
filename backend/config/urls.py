from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.routers import DefaultRouter
from django.http import HttpResponse

from api.views import IngestionJobViewSet, EmissionsRecordViewSet, DashboardViewSet

router = DefaultRouter()
router.register(r"jobs", IngestionJobViewSet, basename="jobs")
router.register(r"records", EmissionsRecordViewSet, basename="records")
router.register(r"dashboard", DashboardViewSet, basename="dashboard")

def health(request):
    return HttpResponse("OK")

urlpatterns = [
    path("health/", health),
    path("admin/", admin.site.urls),
    path("api/auth/token/", obtain_auth_token, name="api-token"),
    path("api/", include(router.urls)),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)