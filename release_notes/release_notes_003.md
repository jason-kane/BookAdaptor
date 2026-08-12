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