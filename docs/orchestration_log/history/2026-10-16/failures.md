# 2026-10-16 — failures

## Brief never written before the dispatch

What happened: I launched the spec reviewer and told the owner its brief existed, but the file was absent; the reviewer found the recon directory empty and started no review.

Mechanism: The brief was written by a command chained behind git rev-parse with two revisions, which failed with fatal; the && skipped the cat, and the trailing echo ok printed anyway; I read the fatal line and did not check the file.

Correction: Rewrote the brief, confirmed it with ls (3378 bytes), resent it to the reviewer; a file a dispatch depends on is listed before the dispatch.
