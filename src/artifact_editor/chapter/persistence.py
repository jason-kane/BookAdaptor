"""Chapter persistence routing during the XML to PostgreSQL rollout.

Compose opts all chapter processes into PostgreSQL together on recreation.
Already-running XML workers retain their backend until their jobs finish.
"""
import os

from . import legacy_persistence


def uses_postgres():
    backend = os.environ.get("CHAPTER_STORAGE", "xml")
    if backend not in ("xml", "postgres"):
        raise ValueError(f"Unknown CHAPTER_STORAGE {backend!r}")
    return backend == "postgres"


def _backend():
    if uses_postgres():
        from artifact_editor.db import chapter_content
        return chapter_content
    return legacy_persistence


def remember_content(chapter):
    return _backend().remember_content(chapter)


def replace_content(chapter, content):
    return _backend().replace_content(chapter, content)


def save_records(chapter, records, *, include_contents=True):
    return _backend().save_records(chapter, records, include_contents=include_contents)
