"""Schema maintenance and one-time legacy chapter import."""
import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from .session import get_engine


def main():
    parser = argparse.ArgumentParser(description="BookAdaptor PostgreSQL schema maintenance")
    parser.add_argument("action", choices=("upgrade", "current", "check", "import-chapter"))
    parser.add_argument("--author")
    parser.add_argument("--title")
    parser.add_argument("--number", type=int)
    parser.add_argument("--language", default="english")
    parser.add_argument("--xml", type=Path, help="Override the legacy XML path")
    args = parser.parse_args()
    if args.action == "import-chapter":
        if not args.author or not args.title or args.number is None:
            parser.error("import-chapter requires --author, --title, and --number")
        from artifact_editor.author.author import Author
        from artifact_editor.chapter.chapter import Chapter, xml_upgrade
        from .chapter_content import import_legacy
        chapter = Chapter(Author(args.author), args.title, args.number, args.language)
        if args.xml:
            chapter.get_xml_fn = lambda: str(args.xml)
        if not Path(chapter.get_xml_fn()).is_file():
            parser.error("Legacy chapter XML does not exist")
        identity, imported = import_legacy(
            chapter, upgrade=lambda chapter, tree: xml_upgrade(chapter, tree, migrate_files=False))
        print("Imported:" if imported else "Already in PostgreSQL:", identity)
        return
    if args.action == "check":
        with get_engine().connect() as connection:
            print("Connected to PostgreSQL database:", connection.scalar(text("select current_database()")))
        return
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    if args.action == "upgrade":
        command.upgrade(config, "head")
    else:
        command.current(config)


if __name__ == "__main__":
    main()
