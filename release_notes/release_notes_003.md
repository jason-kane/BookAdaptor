# Aug 8

I made a mistake that is quite difficult to fix.

## Context

The conversion process currently looks like this.  The exact sequence in many cases is more flexible than this implies.

1. creates an author directory in /active
2. creates a book directory in the author directory
3. creates a plain text file called "book.txt" in that book directory.
4. Open that author/book in BookAdaptor
5. Break it into chapters by choosing a chapter break identifier.
6. Choose and open a chapter, adjust any text cosmetics with the rich text editor.
7. Import as XML (auto-breaks into phrases, generates highlight latex)
8. Assign a speaker to each phrase creating new characters as needed
9. Create voices for each speaking character
10. Generate audio for each phrase
11. Manually choose image locations at phrase breaks
12. Use tooling provided to choose or create images
13. Use tooling provided to generate animations
14. Render video

The core problem is that the phrase auto-breaker at step 7 makes mistakes.  Those mistakes cannot be corrected.  For example, if it chose a too-short phrase and you use the phrase editor to merge it into an ajacent phrase it throws all the subsequent  typesetting off by one.  Similarly when splitting a phrase into two phrases.

The core problem is that once broken into phrases, we have the raw words, the pronounced words, and the latex at a per-phrase level of granularity.  The rich text we have as a delta file which is instead structured around formatting transitions.  

To split/join phrases at the raw words layer, we have to do a parallel split/join to the latex representation.  We can't just go back to the delta and regenerate the latex.

Which is the next major problem.  If the formatting isn't quite what you want and you edit it in the rich text editor, it will generate a whole new xml file.  Any work you've done will be lost.

I *think* I can add phrase_id sorts of decorations to the delta (and add a bunch of deltas to breaks at every phrase breaks).  Then writing the logic for break/join without regenerating the whole XML will be much easier.  Each delta needs to know which phrase it belongs to.  Extra deltas need to be added, so they break on both formatting and phrases.

End result should make both problems addressable.  

Edit the rich text
re-build and re-index the phrases
rename image src to align with re-indexing
delta->latex as many phrases as needed
redraw full text and rainbox
re-generate audio
video durations will be wrong

WIP, getting closer.

Status -

The first convert to xml tags each delta segment in the rich text the phrase number.  It doesn't get the title or author parts right.  

There are a bunch of newline only 'insert' delta objects that do not have phrases.  

I think a pass to put them all on the previous line will work.

I put the rendered pdf on the text page.  I haven't gotten it all tied it so when you 'import chapter' it refreshes the pdf.

The author/title thing bothers me.  The problem is author/title have their own special place in the latex.  They aren't part of the chapter, they are part of the book.

I've also removed the ability to change the chapter name (oops), and by putting a pdf preview on the text page, the typography page is entirely redundant or broken.

text page?
should have an input fields for the cosmetic:
    book title
    chapter title
    author name

NO!  The book is named at the book layer.  author at author.  Translator?  Layer between the book and the language.  Default is the author is also the translator into whatever language it is written in.  The system recognizes author==translator and drops translator.

^ UI goes on the language page; it's not "language" so much as it is "translator" where the language is a prominent attribute.

good insight.  Replace the language page with a translator page.  Use the proper language code for the original language, the code + translator name otherwise.

top of language page selector for translation
add translation:
    translator
    language code
    unicode character set

I like where the new content story can go.

Library (+ Add Author)
    Where to put it.. A "+"" next to "Library"?
    crumb-bar level change seems appropriate, and may be applicable in other depth-contexts.

    thats base.html  // TODO: rename to "library.html"

    the context is "add chapter", so it should always be there regardless of other chapters already exist.

    if there is a chapter, we want to be just to the right of it.  Picking apart base.html I can see we're aready..

    I wonder what it would look _after_ the </wa-breadcrumb-item>?

<pre>
        {% if title %}<wa-breadcrumb-item
          href="{{ url_for('library.book.table_of_contents', author=author.name, title=title) }}">{{title}}</wa-breadcrumb-item>{%
        endif %}
        {% if chapter %}
        <wa-breadcrumb-item href="{{ url_for('library.book.chapter.chapter_base', author=author.name, title=title, chapter_number=chapter.number, language=chapter.language) }}">
          {{chapter.number}}: {{chapter.cosmetic}}
        </wa-breadcrumb-item>
        ** HERE **
        {% endif %}
        
        {% if chapter %}
        <wa-breadcrumb-item href="">{{pretty_language}}</wa-breadcrumb-item>
</pre>
---

The intent is to have it up against the right side of the chapter label so we don't have to fight through a bunch of formatting layers.

should have a non-editable region displaying the title and author, centered with bold title and larger font (close appearance to the latex title).

I got it.  They don't actually _work_ yet, but decent looking entry widgets for new authors, books and chapters.

