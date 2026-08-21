# test cases for artifact_editor/chapter/views.py

import pytest
import logging
from artifact_editor.chapter import views

log = logging.getLogger(__name__)

def test_import_chapter():
    # Test the import_chapter function
    result = views.import_chapter()
    assert result is not None  # Replace with actual expected behavior


def test_compress_ops():
    # Test the compress_ops function
    ops = [
        {"insert": "Hello", "attributes": {"bold": True}},
        {"insert": " World", "attributes": {"bold": True}},
        {"insert": "!", "attributes": {}},
    ]
    compressed = views.compress_ops(ops)
    log.info("Compressed ops: %s", compressed)
    assert len(compressed) == 2  # The first two ops should be merged
    assert compressed[0]["insert"] == "Hello World"
    assert compressed[0]["attributes"]["bold"] is True
    assert compressed[1]["insert"] == "!"
    assert compressed[1]["attributes"] == {}