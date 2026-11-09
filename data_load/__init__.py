"""Dataset preparation and SQLite loading for CookAgent.

Only the runtime API is exported here. The development-time step lives in
``data_load.prepare`` and is imported on demand because it needs pandas.
"""

from .database import open_readonly
from .pipeline import BuildReport, build_database, ensure_database

__all__ = ["BuildReport", "build_database", "ensure_database", "open_readonly"]
