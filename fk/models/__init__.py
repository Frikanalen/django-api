"""
Models for the Frikanalen database.

A lot of the models are business-specific for Frikanalen. There are also
quite a few fields that are related to our legacy systems, but these are
likely to be removed when we're confident that data is properly
transferred.

"""

import logging

from .category import Category  # noqa: F401
from .user import User, UserManager  # noqa: F401
from .video import Video  # noqa: F401
from .video_file import FileFormat, VideoFile  # noqa: F401
from .schedule import Scheduleitem, SchedulePurpose, WeeklySlot  # noqa: F401
from .organization import Organization  # noqa: F401
from .asrun import AsRun  # noqa: F401
from .ingest import IngestJob, Asset  # noqa: F401

logger = logging.getLogger(__name__)
