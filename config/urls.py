from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/', include('chat.urls')),
    path('api/v1/attribution/', include('attribution.urls')),
    path('api/v1/analytics/', include('analytics.urls')),
]
