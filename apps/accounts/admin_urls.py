from django.urls import path

from .views import user_detail, user_list_create

urlpatterns = [
    path('users/', user_list_create, name='admin-user-list'),
    path('users/<int:pk>/', user_detail, name='admin-user-detail'),
]
