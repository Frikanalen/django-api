import pytest
from django.conf import settings
from django.test import Client, override_settings
from django.urls import Resolver404, resolve


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/login/",
        "/logout/",
        "/guide/",
        "/calendar/",
        "/members/video/",
        "/members/video/new/",
        "/members/video/edit/1",
    ],
)
def test_legacy_web_routes_are_not_registered(path: str) -> None:
    with pytest.raises(Resolver404):
        resolve(path)


@pytest.mark.parametrize(
    ("path", "view_name"),
    [
        ("/admin/", "admin:index"),
        ("/api", "api-root"),
        ("/api/tvanytime", "api-tvanytime-home"),
        ("/xmltv/", "xmltv-home"),
    ],
)
def test_operational_web_routes_remain_registered(path: str, view_name: str) -> None:
    assert resolve(path).view_name == view_name


def test_admin_assets_use_the_static_ingress_prefix() -> None:
    assert settings.STATIC_URL == "/static/"


@override_settings(DEBUG=False)
def test_removed_root_renders_the_service_404_page() -> None:
    response = Client().get("/")

    assert response.status_code == 404
    assert b"Page not found" in response.content
    assert b"/login/" not in response.content


def test_csrf_endpoint_mints_a_token_and_sets_the_cookie() -> None:
    client = Client()

    response = client.get("/api/csrf")

    assert response.status_code == 200
    assert response.json()["csrfToken"]
    assert "csrftoken" in response.cookies
