import const
import os

class Book:
    def __init__(self, title):
        self.title = title

class Author:
    def __init__(self, name):
        self.name = name
        self.authordir = os.path.join(const.LIBRARY_DIR, name)
        self.pretty_name = name.replace("_", " ").title()
        self.books = []

    def inventory_books(self):
        for title in sorted(os.listdir(self.authordir)):
            if title in ['__pycache__', ]:
                continue
            
            if not os.path.isdir(os.path.join(self.authordir, title)):
                # file, not a directory.
                continue

            self.add_book(Book(title))
        return self.books
    
    def add_book(self, book: Book):
        self.books.append(book)