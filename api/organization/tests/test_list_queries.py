"""The organization list must not fetch each organization's editor separately."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from fk.models import Organization, User


def _add_organizations(count: int, start: int) -> None:
    for i in range(start, start + count):
        editor = User.objects.create(email=f"org-editor{i}@example.test")
        Organization.objects.create(name=f"Org {i}", editor=editor, fkmember=True)


def _count_list_queries(client: APIClient) -> int:
    with CaptureQueriesContext(connection) as queries:
        response = client.get("/api/organization", {"limit": 100})
    assert response.status_code == 200
    return len(queries)


@pytest.mark.django_db
def test_organization_list_query_count_does_not_grow_with_page_length() -> None:
    client = APIClient()

    _add_organizations(2, start=0)
    short_page = _count_list_queries(client)

    _add_organizations(10, start=2)
    long_page = _count_list_queries(client)

    assert long_page == short_page
