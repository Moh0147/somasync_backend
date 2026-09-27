from django.urls import path

from .views import SyncPullView, SyncPushView

urlpatterns = [
    path('push/', SyncPushView.as_view(), name='sync-push'),
    path('pull/', SyncPullView.as_view(), name='sync-pull'),
]
