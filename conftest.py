"""
Pytest configuration for Frikanalen Django API.

This sets up Django before running any tests.
"""

import os
import django


def pytest_configure():
    """Configure Django settings before running tests."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fkweb.settings.test")
    django.setup()
