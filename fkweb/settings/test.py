from .local import *

########## IN-MEMORY TEST DATABASE
## TODO: Migrate to PostgreSQL
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    },
}
