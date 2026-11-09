"""Persistence conflicts shared by chapter backends and asset jobs."""


class RecordDeletedError(ValueError):
    """A previously loaded record was deleted by another chapter editor."""
