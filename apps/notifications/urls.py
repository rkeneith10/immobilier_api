from django.urls import path

from .views import NotificationListView, NotificationReadAllView, NotificationReadView

app_name = "notifications"
urlpatterns = [
    path("", NotificationListView.as_view(), name="list"),
    path("read-all/", NotificationReadAllView.as_view(), name="read-all"),
    path("<uuid:pk>/read/", NotificationReadView.as_view(), name="read"),
]
