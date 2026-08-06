# Stub file for django-environ
#
# Env deliberately names its casting methods after the builtins (env.str,
# env.int, ...). Inside the class body those definitions shadow the real
# builtins for every later annotation, so all builtin types here are
# spelled builtins.* -- the same convention typeshed uses for classes
# with such methods.

import builtins
from collections.abc import Callable
from pathlib import Path as _PathlibPath
from typing import IO, Any
from urllib.parse import ParseResult

__copyright__: builtins.str
__version__: builtins.str
__license__: builtins.str
__author__: builtins.str
__author_email__: builtins.str
__maintainer__: builtins.str
__maintainer_email__: builtins.str
__url__: builtins.str
__description__: builtins.str

class ImproperlyConfigured(Exception): ...

REDIS_DRIVER: builtins.str
DJANGO_POSTGRES: builtins.str
PYMEMCACHE_DRIVER: builtins.str

def choose_rediscache_driver() -> builtins.str: ...
def choose_postgres_driver() -> builtins.str: ...
def choose_pymemcache_driver() -> builtins.str: ...

class Env:
    URL_CLASS: type[ParseResult]

    def __init__(self, **scheme: Any) -> None: ...
    # Env.__call__ is get_value: env("VAR") reads with an optional cast.
    def __call__(
        self,
        var: builtins.str,
        cast: Callable[..., Any] | None = ...,
        default: Any = ...,
        parse_default: builtins.bool = False,
    ) -> Any: ...
    def str(
        self, var: builtins.str, default: Any = ..., multiline: builtins.bool = False
    ) -> builtins.str: ...
    def bytes(
        self, var: builtins.str, default: Any = ..., encoding: builtins.str = "utf8"
    ) -> builtins.bytes: ...
    def bool(self, var: builtins.str, default: Any = ...) -> builtins.bool: ...
    def int(self, var: builtins.str, default: Any = ...) -> builtins.int: ...
    def float(self, var: builtins.str, default: Any = ...) -> builtins.float: ...
    def json(self, var: builtins.str, default: Any = ...) -> Any: ...
    def list(
        self, var: builtins.str, cast: Callable[..., Any] | None = ..., default: Any = ...
    ) -> builtins.list[Any]: ...
    def tuple(
        self, var: builtins.str, cast: Callable[..., Any] | None = ..., default: Any = ...
    ) -> builtins.tuple[Any, ...]: ...
    def dict(
        self, var: builtins.str, cast: type = ..., default: Any = ...
    ) -> builtins.dict[Any, Any]: ...
    def url(self, var: builtins.str, default: Any = ...) -> ParseResult: ...
    def db_url(
        self,
        var: builtins.str = "DATABASE_URL",
        default: Any = ...,
        engine: builtins.str | None = ...,
    ) -> builtins.dict[builtins.str, Any]: ...
    def db(
        self,
        var: builtins.str = "DATABASE_URL",
        default: Any = ...,
        engine: builtins.str | None = ...,
    ) -> builtins.dict[builtins.str, Any]: ...
    def cache_url(
        self,
        var: builtins.str = "CACHE_URL",
        default: Any = ...,
        backend: builtins.str | None = ...,
    ) -> builtins.dict[builtins.str, Any]: ...
    def cache(
        self,
        var: builtins.str = "CACHE_URL",
        default: Any = ...,
        backend: builtins.str | None = ...,
    ) -> builtins.dict[builtins.str, Any]: ...
    def email_url(
        self,
        var: builtins.str = "EMAIL_URL",
        default: Any = ...,
        backend: builtins.str | None = ...,
    ) -> builtins.dict[builtins.str, Any]: ...
    def email(
        self,
        var: builtins.str = "EMAIL_URL",
        default: Any = ...,
        backend: builtins.str | None = ...,
    ) -> builtins.dict[builtins.str, Any]: ...
    def search_url(
        self,
        var: builtins.str = "SEARCH_URL",
        default: Any = ...,
        engine: builtins.str | None = ...,
    ) -> builtins.dict[builtins.str, Any]: ...
    def channels_url(
        self,
        var: builtins.str = "CHANNELS_URL",
        default: Any = ...,
        backend: builtins.str | None = ...,
    ) -> builtins.dict[builtins.str, Any]: ...
    def channels(
        self,
        var: builtins.str = "CHANNELS_URL",
        default: Any = ...,
        backend: builtins.str | None = ...,
    ) -> builtins.dict[builtins.str, Any]: ...
    def path(self, var: builtins.str, default: Any = ..., **kwargs: Any) -> _PathlibPath: ...
    def get_value(
        self,
        var: builtins.str,
        cast: Callable[..., Any] | None = ...,
        default: Any = ...,
        parse_default: builtins.bool = False,
    ) -> Any: ...
    @classmethod
    def parse_value(cls, value: Any, cast: Callable[..., Any]) -> Any: ...
    @classmethod
    def db_url_config(
        cls, url: builtins.str | ParseResult, engine: builtins.str | None = ...
    ) -> builtins.dict[builtins.str, Any]: ...
    @classmethod
    def cache_url_config(
        cls, url: builtins.str | ParseResult, backend: builtins.str | None = ...
    ) -> builtins.dict[builtins.str, Any]: ...
    @classmethod
    def email_url_config(
        cls, url: builtins.str | ParseResult, backend: builtins.str | None = ...
    ) -> builtins.dict[builtins.str, Any]: ...
    @classmethod
    def channels_url_config(
        cls, url: builtins.str | ParseResult, backend: builtins.str | None = ...
    ) -> builtins.dict[builtins.str, Any]: ...
    @classmethod
    def search_url_config(
        cls, url: builtins.str | ParseResult, engine: builtins.str | None = ...
    ) -> builtins.dict[builtins.str, Any]: ...
    @classmethod
    def read_env(
        cls,
        env_file: builtins.str | None = ...,
        overwrite: builtins.bool = False,
        parse_comments: builtins.bool = False,
        encoding: builtins.str = "utf8",
        **overrides: Any,
    ) -> None: ...

class FileAwareEnv(Env): ...

class Path:
    root: str
    def __init__(self, start: str = "", *paths: str, **kwargs: Any) -> None: ...
    def path(self, *paths: str, **kwargs: Any) -> _PathlibPath: ...
    def file(self, name: str, *args: Any, **kwargs: Any) -> IO[Any]: ...
    def rfind(self, *args: Any, **kwargs: Any) -> int: ...
    def find(self, *args: Any, **kwargs: Any) -> int: ...

class FileAwareMapping:
    def __init__(self, env: dict[str, str] | None = ..., cache: bool = True) -> None: ...
