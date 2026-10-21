"""Persistent input records for rendered video frames."""
import json
import os
import tempfile


def metadata_filename(frame_filename):
    return os.path.splitext(frame_filename)[0] + ".json"


def frame_is_current(frame_filename, inputs):
    if not os.path.isfile(frame_filename):
        return False
    try:
        with open(metadata_filename(frame_filename)) as source:
            return json.load(source) == inputs
    except (OSError, ValueError):
        return False


def invalidate_frame_metadata(frame_filename):
    try:
        os.unlink(metadata_filename(frame_filename))
    except FileNotFoundError:
        pass


def save_frame_metadata(frame_filename, inputs):
    """Publish metadata atomically, only after the frame was saved successfully."""
    filename = metadata_filename(frame_filename)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=os.path.dirname(filename), suffix=".json.tmp", delete=False
        ) as target:
            temporary = target.name
            json.dump(inputs, target, indent=2)
            target.write("\n")
        os.replace(temporary, filename)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
