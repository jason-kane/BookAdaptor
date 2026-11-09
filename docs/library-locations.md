# Library locations

* **Active** is the fastest working disk, mounted at `/active`.
* **Shelf** is a read/write working disk at `/home/jkane/books`. Books can be
  edited, imported, rendered, trained, played, and assembled while on Shelf.
* **Storage** is the read-only archive at `/storage`. Move a book to Active or
  Shelf before editing or processing it.

The Library's location filter includes all three tiers. Each book has move
buttons for the other locations, and its book page shows the current location.
Transfers use the CPU Queue, check available space, copy and hash-verify files,
then remove the source. Queued or running work prevents a move. Duplicate
physical copies are shown as conflicts; no copy is silently chosen or merged.

Shelf has the same absolute mount path in the web app, workers, trainer, and
Home. The app maintains validated book links in Active's catalog pointing to
Shelf. Those links consume no extra copy of the chapter data, preserve existing
asset paths, and are excluded from Active's physical book inventory. Arbitrary
links and links inside transferred books remain unsupported. Storage books are
never linked into the working catalog.

Existing Shelf books appear automatically. Author profiles are kept in the
shared working catalog, seeded from Shelf when needed. To rename an author
across disks, first move that author's Shelf books to Active; this prevents
leaving book paths tied to the old author identity.

All services with the working library need the Shelf mount before Shelf work is
submitted. Changes to mount configuration require container recreation.
