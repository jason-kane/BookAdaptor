import const
import os

class Book:
    def __init__(self, title: str):
        self.title = title

class Author:
    def __init__(self, name: str):
        self.name = name
        self.authordir = os.path.join(const.LIBRARY_DIR, name)
        self.pretty_name = name.replace("_", " ").title()
        self.books: list[Book] = []

    def inventory_books(self):
        for title in sorted(os.listdir(self.authordir)):
            if title.startswith('.') or title == '__pycache__':
                continue
            
            if not os.path.isdir(os.path.join(self.authordir, title)):
                # file, not a directory.
                continue

            self.add_book(Book(title))
        return self.books
    
    def add_book(self, book: Book) -> None:
        self.books.append(book)