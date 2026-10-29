"""Frikanalen's schedule as NorDig TV-Anytime metadata.

The NorDig EPG/Event metadata exchange format is TV-Anytime (ETSI TS
102 822-3-1 v1.11.2) with a handful of NorDig classification schemes
layered on top; see `NorDig TVA Implementation Guidelines v1.4`. It is
the format Nordic distributors expect to pull an EPG in, which XMLTV --
the other feed this app serves -- is not.

`document.build` turns schedule items into the XML; `views` serves it.
"""

from .document import build  # noqa: F401
