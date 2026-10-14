from django.urls import path

from . import views

urlpatterns = [
    path('enhance/', views.enhance, name='audio-enhance'),
    path('enhance/<uuid:job_id>/', views.enhance_job, name='audio-enhance-job'),
    path('enhance/<uuid:job_id>/result/', views.enhance_result, name='audio-enhance-result'),
    path('quota/', views.quota, name='audio-quota'),
]
