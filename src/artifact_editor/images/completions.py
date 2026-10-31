"""Chapter illustration completion messages for the editor's image reveals."""
import json
import logging
from pathlib import Path
from urllib.parse import quote, urlencode

from redis import RedisError

log = logging.getLogger(__name__)


def normalize_chapter_identity(value):
    identity = {}
    for key in ("author", "title", "language"):
        part = value.get(key)
        if (not isinstance(part, str) or not part or part in (".", "..")
                or any(character in part for character in ("/", "\\", "\x00"))):
            raise ValueError(f"Invalid chapter {key}")
        identity[key] = part
    number = value.get("chapter_number")
    if isinstance(number, bool) or not str(number).isdigit():
        raise ValueError("Invalid chapter number")
    identity["chapter_number"] = int(number)
    return identity


def completed_image(identity, image_index, generation_id):
    author, title, language = (quote(identity[key], safe="") for key in ("author", "title", "language"))
    index = int(image_index)
    if index < 0 or not isinstance(generation_id, str) or not generation_id:
        raise ValueError("Invalid generated image identity")
    return {
        "id": generation_id,
        "index": index,
        "url": (
            f"/library/{author}/{title}/{identity['chapter_number']}/{language}/images/640/{index}.png?"
            + urlencode({"v": generation_id})
        ),
    }


def chapter_image_snapshot(identity, library_dir=None):
    """Read one reconnect snapshot without constructing or modifying a Chapter."""
    identity = normalize_chapter_identity(identity)
    if library_dir is None:
        import const
        library_dir = const.LIBRARY_DIR
    root = Path(library_dir).resolve()
    chapter_dir = root / identity["author"] / identity["title"] / "chapter" / f"{identity['chapter_number']:04}"
    if not chapter_dir.resolve().is_relative_to(root):
        raise ValueError("Chapter is outside the library")
    xml_path = chapter_dir / identity["language"] / "chapter.xml"
    images = []
    for source_path in (xml_path, chapter_dir / "book.xml"):
        if source_path.is_file() and not source_path.resolve().is_relative_to(root):
            raise ValueError("Chapter source is outside the library")
    from artifact_editor.chapter_source import read_chapter_source
    soup = read_chapter_source(xml_path)
    if soup is not None:
        for image in soup.find_all("image"):
            paragraph = image.find_parent("paragraph")
            if paragraph is None or not image.get("src") or not image.get("image_generation_id"):
                continue
            try:
                image_path = (
                    chapter_dir / "paragraphs" / f"{int(paragraph['index']):06}" / image["src"]
                ).resolve()
                if not image_path.is_relative_to(root) or not image_path.is_file():
                    continue
                images.append(completed_image(identity, image["index"], image["image_generation_id"]))
            except (KeyError, TypeError, ValueError):
                continue
    return {"state": "chapter_image_snapshot", **identity, "images": images}


def publish_image_completion(chapter, image, client=None):
    """Publish only after the image selection and generation ID have been saved."""
    try:
        from task_queue import CHAPTER_IMAGE_EVENTS
        if client is None:
            from artifact_editor.cache import r
            client = r
        identity = normalize_chapter_identity({
            "author": chapter.author.name, "title": chapter.title,
            "chapter_number": chapter.number, "language": chapter.language,
        })
        payload = {
            "state": "chapter_image_complete", **identity,
            "image": completed_image(identity, image["index"], image["image_generation_id"]),
        }
        client.publish(CHAPTER_IMAGE_EVENTS, json.dumps(payload))
        return True
    except (RedisError, AttributeError, KeyError, TypeError, ValueError):
        # A disconnected preview must not turn a successfully saved image into
        # a failed generation. Reconnection snapshots recover missed notices.
        log.warning("Could not publish chapter image completion", exc_info=True)
        return False
