# Pronunciation Fixup (Aug 4, 2026)

I kind of sort of broke just about everything, but I think it will be worth it.  Let me explain, with some context.

As far as the flask webservice is concerned, the universe begins at src/artifact_editor/app.py

I'm using Blueprints.

(https://flask.palletsprojects.com/en/stable/blueprints/)[https://flask.palletsprojects.com/en/stable/blueprints/]

The whole thing becomes a tree.

```python
app.register_blueprint(
    library,
    url_prefix="/library"
)
```

Where `library` is the Blueprint I want to have served at /library.

## Key Point  
Within the library view that defines the `library` Blueprint, _nothing_ knows that the blueprint is mounted at /library.

As long as I'm disiplined about using `url_for()` to generate the URLs that need to match against views, I can rename url path fragments, move them around, whatever.  Huge flexibility.

It has worked that way for a long while.  The problem is with the arguments.

Everything deeper than /library/author/title/chapternum/language/ starts with boilerplate like:

```python
@bp.route("/<int: image_index>", methods=["POST"])
def create_image(author, title, chapter_number, language, image_index):
    """
    Add a new character to the chapter.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
```

After the change:

```python
@bp.route("/<int: image_index>", methods=["POST"])
def create_image(image_index):
    """
    Add a new character to the chapter.
    """
    chapter = g.chapter
```

Views only need to worry about their own stuff.  The `g` is more magical
than I would like but it's a pretty solid trade.

Similarly, previously a `url_for()` would always be a monster like:

```python
url_for('library.book.chapter.images.base', author=author.name, title=title, chapter_number=chapter.number, language=language, image_index=0)
```

but can now be:

```python
url_for('library.book.chapter.images.base', image_index=0)
```

## How?

Two new bits of boilerplate in every view after the Blueprint() creation.  I could also abstract these into a `bp = create_blueprint()` sort of thing but I'm okay with this for now.

```python
bp = Blueprint(
    "characters",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)

@bp.url_defaults
def my_url_defaults(endpoint, values):
    chapter_url_defaults_helper(endpoint, values)

@bp.url_value_preprocessor
def my_url_value_preprocessor(endpoint, values):
    chapter_url_value_preprocessor(endpoint, values) 
```

where:

```python
def chapter_url_defaults_helper(endpoint, values):
    """
    Automatically add author, title, chapter_number, and language to the URL
    if they are not already present.
    """
    log.info("bp.url_defaults() called", endpoint=endpoint, values=values)
    
    if 'author' not in values and hasattr(g, "author"):
        values["author"] = g.author.name
    if 'title' not in values and hasattr(g, "title"):
        values["title"] = g.title
    if 'chapter_number' not in values and hasattr(g, "chapter_number"):
        values["chapter_number"] = int(g.chapter_number)
    if 'language' not in values and hasattr(g, "language"):
        values["language"] = g.language
    return endpoint, values


def chapter_url_value_preprocessor(endpoint, values: dict):
    """
    Extract author, title, chapter_number, and language from the URL and store
    them in the Flask global context (g).
    """
    log.info("Pulling higher nesting values", endpoint=endpoint, values=values)
    if 'author' in values:
        g.author = Author(values.pop("author"))
    if 'title' in values:
        g.title = str(values.pop("title"))
    if 'chapter_number' in values:
        g.chapter_number = int(values.pop("chapter_number"))
    if 'language' in values:
        g.language = str(values.pop("language"))
    g.chapter = Chapter(
        author=g.author,
        title=g.title,
        number=g.chapter_number,
        language=g.language,
    )
    return endpoint, values
```

The way this works is when Flask parses a URL to determine which arguments are required it will call url_value_preprocessor.  Only values that survive will be expected.

By using .pop() to remove parameters the @bp.route() decorated view no longer requires them.  We don't want them lost, so we stash them in `g`.

`g` in flask is an [Application Global](https://flask.palletsprojects.com/en/stable/api/#application-globals)

Which is great and solves most of the problem.  But ... url_for() breaks -- it can't build the URL string without values for all the `/<variable>/`

That is where `url_defaults` comes in.  When a `url_for()` is generated against this blueprint, url_defaults is called.  It will fill-in the missing values based on the contents of `g`.

## So broken..

The process of converting all the URL routes from the 'old' style to the 'new' one is going to take a few sessions.  I got all the base routes but I am absolutely sure there are dozens remaining to-do.

As a matter of good code hygiene I'm restricting `g` to views.  I want non-views to be usable in non-flask contexts.  While it is tempting, throwing globals around is a good way to transmute lead into shit.
