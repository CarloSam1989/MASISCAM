from django.contrib.auth.views import LogoutView
from django.urls import path
from .views import MasiscamLoginView, seleccionar_empresa
app_name = "accounts"
urlpatterns = [path("login/", MasiscamLoginView.as_view(), name="login"), path("logout/", LogoutView.as_view(), name="logout"), path("empresa/", seleccionar_empresa, name="seleccionar_empresa")]
