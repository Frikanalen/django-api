


def create_auth_token(_sender, instance=None, created=False, **_kwargs):
    from rest_framework.authtoken.models import Token
    """Create a new token for every new user, this is needed for auth to the API"""
    if created:
        Token.objects.create(user=instance)
