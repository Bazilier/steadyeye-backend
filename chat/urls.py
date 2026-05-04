from django.urls import path

from . import views

urlpatterns = [
    path('health/', views.health, name='health'),
    path('messages/', views.messages, name='messages'),
    path('telegram/webhook/', views.telegram_webhook, name='telegram-webhook'),
]
