from django.urls import path

from .views import FavoriteListView

app_name = "interactions"
urlpatterns = [path("", FavoriteListView.as_view(), name="favorite-list")]
