Research
========

Would https://github.com/Linwei94/gnvitop be more useful than glances?

Home
====
These aren't as modular as they seem to be.


Library
=======
Split into Local, Shelf, Shared and Global

Local is your "active"
Shelf is your "shelf", still local, still private.
shared is published to your public github
global pulls basic title metadata and cover images from an online source.

TOOD: Method "import" someone elses shared github.  It will work like a remote shelf (downloading single books on-demand)

Choosing a shelf or global book copies it into active.

Book Page
=========

/library/authorname/bookname/

Button to shelve the current book
Button to share the current book to the global library
Indicator if the current book has changes since the most recent commit
    Button to save (local git commit) the current book if there are any changes
    Button to view the diff between the current book and (local git or global)

Full Book Videos (concatenation of chapters with one fullscreen chapter page between each), title and author page prefixing.

Choosing the chapter should take you to the chapter page.  *Not* the text page.

General Menu
============

I do not like the workshops dropdown as the primary method of navigation.

Chapter Page
============
/library/authorname/bookname/chapternumber/language

Multi-language support is .. well, it is shit.

Include the chapter text here?  If only to make it easy to copy-paste the chapter title if there is one.


Text Workshop
=============
The vertical stack layout is ... well, it is lame.

Make the plain text width narrower, to align more approximatly with the width of the rendered page.

Move the XML version so they can sit side-by-side at 1920x
The metadata should be above them both
Remove "extract characters" button
Move "YouTube URL" to the chapter page -- actually, move everything except paragraph technique to the chapter page.

Character Workshop
==================

The Narrator should be special cased so it has voice controls, but no description or image.

The "Global Character" stuff is buggy junk.

Tab panels for each character would be an improvement.  The vertical flow is very tedious in a book with many characters.

Characters have three potential scopes, global, book and chapter.

We don't have a good way to capture changes to a character description over the course of the story.  It mostly doens't matter, but there are times it will really matter.

Phrase Workshop
===============

Generate All Missing Audio doesn't work; I haven't tried the other top level buttons in a while.  Assumed to be broken.

There isn't an important difference between "(Re)generate Audio" and "Re-pronounce".  Make them one button.1

The "split phrase" system is kind of shit, but with some modest effort it can be fixed.

The "Animation" and "Transition" checkboxes are pointless.

There appears to be a chicken/egg problem between Phrase workshop and rendering the latex.

Pronunciation Guide
===================
I like this one.  The SoundsLike approach is quite powerful as a technique to both create new and correct old IPA representations of words.

There is still the Bow problem.  Correct pronunciation depends on context to identify the correct word.

Pronunciation guide menu thinks it is Audio>Pronunciation Guide ("Phrases" used to be "Audio")

Timeline
========
Opening the timeline is ... well, ouch.  It's trying to call Comfy to generate an audio but something fell off the rails.  Timeline is broken.

Typography Workshop
===================
The "Text Structure" select is borked

Hyper Redraw works.  Now that  the old redraw is obsolete I don't think this needs to be called "Hyper" anymore.

When Hyper finishes, it doesn't do anything helpful.  You have to manually reload the page.

Similarly, "Draw Missing Text" is obsolete, "Redraw All Text" is obsolete.  I don't think Clear Highlight Dimensions or Build Missing Highlight Geometry matter anymore either.

Reset Camera / Evaluate Camera Rate are both important, but are not really about typography.

Image Workshop
==============

The top image nav (forex) is both buggy and a total failure at different resolutions.

As far as I'm aware all the top buttons are broken.

The highlighted xml thing, that I like.

Scene is redundant with Prompt now.  Prompt options other than workflow are broken; we should just lock it to workflow and remove the selector, I only hesitate since that whole plugin system took so long to get working nicely.

Upload is broken.

I like Citation, but haven't actually used it for anything yet.

I assume both Camera and Transition are broken since I haven't used them in a long while.

Frame Workshop
==============
Broken _HARD_ (500)

Music Workshop
==============
Doesn't do anything yet, it's a placeholder for a fun notion.

Video Workshop
==============
We shouldn't need "Regenerate Master Plan" anymore.  Everything that would benefit from a MP refresh should do it automatically now (only takes a momement)

We should only need "Render" and "Clear", where Render tries to generate everything that isn't already done, and clear wipes the slate.

Publish
=======

Publish to github just barely works.  This will be absorbed into the Local/Shelf/Global stuff?

Example of "Published" book:
https://github.com/jason-kane/library
The idea is to find the smallest thing that can be imported to re-create the "same" output video.