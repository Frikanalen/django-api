# See phonenumber.pyi for why this stub exists and how far it goes.

from django.db import models

from phonenumber_field.phonenumber import PhoneNumber

# Assigning a plain string is allowed -- the field parses it -- but reading
# the attribute back always gives a PhoneNumber. That is the two-parameter
# Field[SetType, GetType] shape django-stubs uses.
class PhoneNumberField(models.CharField[str | PhoneNumber, PhoneNumber]): ...
