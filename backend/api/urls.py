from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import IngestionJobViewSet, EmissionsRecordViewSet, DashboardViewSet
router = DefaultRouter()
router.register(r'jobs', IngestionJobViewSet, basename='jobs')
router.register(r'records', EmissionsRecordViewSet, basename='records')
router.register(r'dashboard', DashboardViewSet, basename='dashboard')
urlpatterns = [path('', include(router.urls))]
