import os

from flask import (
    url_for,
)

import logger
from artifact_editor import (
    config,
    tools,
)

log = logger.log(__name__)


class Book:
    def __init__(self, author, title):
        self.author = author
        self.title = title
        
        self.bookdir = tools.get_bookdir(author.name, title)
        log.info('Bookdir for "%s" by "%s": %s', title, author.name, self.bookdir)
        self.bookurl = tools.get_bookurl(author.name, title)
        self.config = config.get_config(
            chapterdir=os.path.join(
                author.name,
                title
            )
        )
        self.subtitle = self.config.get("subtitle", "")
        self.set_book_metadata_url = url_for(
            "library.book.set_book_metadata",
            author=self.author.name,
            title=self.title,
        )

    def input_field(self, label, key, value):
        return f"""
        <div class="wa-cluster">
            <div class="label">{label}</div>
            <wa-input
                name="{key}"
                hx-target="#book_metadata"
                hx-put="{self.set_book_metadata_url}"
                value="{value}"></wa-input>
        </div>
        """

    def choice_field(self, label, key, value, choices):
        options = []
        for v, pretty in choices:
            options.append(
                f'<wa-option value="{v}" {"selected" if v == value else ""}>{pretty}</wa-option>'
            )

        return f"""
        <div class="wa-cluster">
            <div class="label">{label}</div>
            <wa-select
                value="{value}"
                hx-put="{self.set_book_metadata_url}"
                hx-swap="outerHTML transition:true"
                hx-target="#book_metadata"
                hx-trigger="change"
                name="{key}"
            >
                {"".join(options)}
            </wa-select>      
        </div>
        """

    def save_config(self):
        config.save_config(
            chapterdir=os.path.join(
                self.author.name,
                self.title
            ),
            config=self.config
        )

