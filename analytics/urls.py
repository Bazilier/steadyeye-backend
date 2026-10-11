from django.urls import path

from . import views

urlpatterns = [
    path('refresh/', views.refresh, name='analytics_refresh'),
    path('webhooks/revenuecat/', views.revenuecat_webhook, name='rc_webhook'),
    path('duo-report/', views.duo_report, name='duo_report'),
]
