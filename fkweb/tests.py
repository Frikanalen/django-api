import logging

import pytest
from django.conf import settings
from django.test import Client, override_settings
from django.urls import Resolver404, resolve
from django.utils.log import AdminEmailHandler


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


def _all_log_handlers() -> list[logging.Handler]:
    loggers = [logging.getLogger(), *logging.Logger.manager.loggerDict.values()]
    return [h for lg in loggers if isinstance(lg, logging.Logger) for h in lg.handlers]


def test_no_logger_mails_errors() -> None:
    """Errors go to the logs, never to mail, so turning on SMTP sends nothing by itself."""
    assert not any(isinstance(h, AdminEmailHandler) for h in _all_log_handlers())
    assert settings.ADMINS == []
    assert settings.MANAGERS == []


def test_a_server_error_sends_no_mail(mailoutbox: list) -> None:
    logging.getLogger("django.request").error("Internal Server Error: /boom", exc_info=False)
    logging.getLogger("gunicorn.error").error("worker died")
    assert mailoutbox == []
