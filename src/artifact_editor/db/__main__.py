"""Database maintenance: python -m artifact_editor.db {upgrade,current,check}."""
import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from .session import get_engine


def main():
    parser = argparse.ArgumentParser(description="BookAdaptor PostgreSQL schema maintenance")
    parser.add_argument("action", choices=("upgrade", "current", "check"))
    args = parser.parse_args()
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
