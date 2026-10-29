# Hand-written stub for django-phonenumber-field, which ships no py.typed
# marker and so cannot be read by mypy at all.
#
# Deliberately covers only what this codebase touches (see fk/models/user.py
# and api/organization/serializers.py). A stub declares the module's whole
# surface, so reaching for something missing here is a type error -- add its
# line when you need it.

class PhoneNumber:
    # Formatted representations; properties upstream, not methods.
    @property
    def as_international(self) -> str: ...
    def is_valid(self) -> bool: ...
    def __str__(self) -> str: ...
