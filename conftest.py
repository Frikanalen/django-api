import pytest
from pwned_passwords_django import api, exceptions


@pytest.fixture(autouse=True)
def pwned_passwords_offline(monkeypatch):
    """Tests never call Have I Been Pwned.

    By default the API behaves as unreachable, which makes the validator
    fall back to Django's common-password list. A test that wants a
    verdict sets `pwned.breached` to the passwords HIBP should report.
    """

    class Fake:
        breached: set[str] | None = None

        def check_password(self, password: str) -> int:
            if self.breached is None:
                raise exceptions.PwnedPasswordsError(
                    "offline in tests",
                    code=exceptions.ErrorCode.REQUEST_ERROR,
                    params={},
                )
            return 1234 if password in self.breached else 0

    fake = Fake()
    monkeypatch.setattr(api.default_client, "check_password", fake.check_password)
    return fake
