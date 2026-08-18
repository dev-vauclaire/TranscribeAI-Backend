"""Database migration application package."""

from migration.main import main, upgrade_database

__all__ = ["main", "upgrade_database"]
