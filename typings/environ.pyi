# Hand-written stub for django-environ, which ships no types of its own.
#
# Deliberately covers only what this codebase calls (see fkweb/settings/);
# a stub declares the module's complete surface to mypy, so using a
# method missing here is a type error -- add its line when you need it.
#
# Env names its casting methods after the builtins (env.str, ...), and
# inside the class body those definitions shadow the real builtins for
# every later annotation, so builtin types here are spelled builtins.*
# -- the same convention typeshed uses for classes with such methods.

import builtins
from typing import Any

class ImproperlyConfigured(Exception): ...

class Env:
    def __init__(self, **scheme: Any) -> None: ...
    def str(
        self, var: builtins.str, default: Any = ..., multiline: builtins.bool = False
    ) -> builtins.str: ...
    def db(
        self,
        var: builtins.str = "DATABASE_URL",
        default: Any = ...,
        engine: builtins.str | None = ...,
    ) -> dict[builtins.str, Any]: ...
    def cache(
        self,
        var: builtins.str = "CACHE_URL",
        default: Any = ...,
        backend: builtins.str | None = ...,
    ) -> dict[builtins.str, Any]: ...
    @classmethod
    def read_env(
        cls,
        env_file: builtins.str | None = ...,
        overwrite: builtins.bool = False,
        parse_comments: builtins.bool = False,
        encoding: builtins.str = "utf8",
        **overrides: Any,
    ) -> None: ...
