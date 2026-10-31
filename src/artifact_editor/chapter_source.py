"""Read-only chapter snapshots for notifications, previews and exports."""
import os
from pathlib import Path

from bs4 import BeautifulSoup


def read_chapter_source(path):
    """Resolve the canonical language/chapter.xml path against active storage.

    Unimported chapters can still be previewed from their legacy source. Once
    imported, even an empty database chapter takes precedence over that file.
    """
    path = Path(path)
    if os.environ.get("CHAPTER_STORAGE", "xml") == "postgres":
        from artifact_editor.db.chapter_content import read_content
        tree = read_content(author=path.parents[4].name, title=path.parents[3].name,
                            number=int(path.parents[1].name), language=path.parent.name)
        if tree is not None:
            return tree
    if not path.is_file():
        path = path.parents[1] / "book.xml"
    return BeautifulSoup(path.read_bytes(), "xml") if path.is_file() else None
