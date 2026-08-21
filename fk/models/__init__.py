"""
Models for the Frikanalen database.

A lot of the models are business-specific for Frikanalen. There are also
quite a few fields that are related to our legacy systems, but these are
likely to be removed when we're confident that data is properly
transferred.

"""

import logging

from .asrun import AsRun  # noqa: F401
from .category import Category  # noqa: F401
from .ingest import IngestJob, IngestState  # noqa: F401
from .organization import Organization  # noqa: F401
from .program_image import ImageMediaType, ImageRole, ProgramImage  # noqa: F401
from .schedule import (  # noqa: F401
    Scheduleitem,
    SchedulePurpose,
    WeeklySlot,
    airtime_end,
)
from .series import Series  # noqa: F401
from .user import User, UserManager  # noqa: F401
from .video import Video  # noqa: F401
from .video_file import VideoFile, VideoFileVariant  # noqa: F401

logger = logging.getLogger(__name__)
