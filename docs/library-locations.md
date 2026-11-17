# Library locations

* **Active** is the fastest working disk and authoritative catalog, mounted at
  `/active`.
* **Shelf** is a read/write working disk at `/home/jkane/books`. Books can be
  edited, imported, rendered, trained, played, and assembled while on Shelf.
* **Storage** is the read-only archive at `/storage`. Move a book to Active or
  Shelf before editing or processing it.

The Library inventories `/active` only. A physical book directory there is the
catalog entry even when selected asset paths point into Shelf. A whole-book
move to Shelf or Storage leaves a managed book link under `/active`, so the
catalog remains authoritative and each book appears once. Storage books remain
read-only. Transfers use the CPU Queue, check available space, copy and
hash-verify files, then remove the source. Queued or running work prevents a
move.

Shelf has the same absolute mount path in the web app, workers, trainer, and
Home. The app maintains validated whole-book links in Active's catalog pointing
to Shelf or Storage. Shelf links are writable; Storage links are read-only.
Split books keep their real book directory in Active and may use links for large
media directories or files. Chapter video generation creates the parallel
Shelf paths and Active links automatically. Arbitrary whole-book links are
rejected.

Existing Shelf and Storage books are added to Active through managed whole-book
links. Author profiles are kept in the shared working catalog, seeded from
Shelf when needed. To rename an author across disks, first move that author's
Shelf or Storage books to Active; this prevents leaving book paths tied to the
old author identity.

All services with the working library need the Shelf mount before Shelf work is
submitted. Changes to mount configuration require container recreation.
