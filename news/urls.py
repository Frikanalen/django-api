from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import BulletinViewSet

# Trailing-slash-free, matching the router in api/urls.py: one URI per
# resource across the whole API rather than two conventions side by side.
router = SimpleRouter(trailing_slash=False)
router.register(r"bulletins", BulletinViewSet)

urlpatterns = [
    path("", include(router.urls)),
]
