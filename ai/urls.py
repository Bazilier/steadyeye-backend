from django.urls import path

from . import views

urlpatterns = [
    path('optimize/', views.optimize, name='ai-optimize'),
    path('split/', views.split, name='ai-split'),
]
