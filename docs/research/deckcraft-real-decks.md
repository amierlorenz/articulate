# DeckCraft on real Canvas decks

Ticket: [Test DeckCraft on real Canvas decks](https://github.com/amierlorenz/articulate/issues/28). Run 2026-11-18 on DeckCraft 0.18.1, Keynote 14.5, macOS 27, M2 Pro. Scripts are in `deckcraft-real-decks/`.

## Control protocol (as found)

- `DeckCraft --control PORT` serves newline-delimited JSON on 127.0.0.1, one request per connection: `{"id":1,"method":…,"params":{…}}` → `{"id","ok","result"|"error"}`.
- Top-level methods: `document.inspect`, `app.quit`, `command`. Everything else is `{"method":"command","params":{"id":"<command id>","params":{…}}}`.
- `file.open {path}` → `{document, slides, lostSlides, damagedMedia, missingFonts}`.
- `document.inspect` → per slide: `title`, `notes`, `hidden`, `animations` (count), `layout`, `shapes` (count); no body text.
- `slide.inspect {index}` → shapes with `kind` and `text`. SmartArt comes back as `group` with no text; group and table text is missing.
- `file.export {path:"x.pdf"}`; options that work: `includeHidden:true` (otherwise hidden slides are dropped from the PDF), `dpi:N` (image downsampling).
- `file.export {path:"dir/s.png", all:true}` → `s1.png…sN.png`, 1920 px wide, hidden slides included. Without `all` it writes only the current slide. The folder must exist.
- `file.close` (command), `app.quit` (**method**, not a command; it was wrong in #14).
- Unknown params are silently ignored, so verify the output (page count), not the reply.

## Pitfalls

- If DeckCraft is already running, plain `open -a` hands off to that instance: no env, no port, no error. Always launch with `open -n -g -j --env DECKCRAFT_NO_PREFS=1 --env DECKCRAFT_NO_RECOVERY=1 -a DeckCraft --args --control PORT`.
- If the port is taken, the new instance logs `control server failed to bind … Address already in use` and **keeps running** headless. Pick a free port, wait ≤10 s for it, and otherwise kill that pid.

## Decks

| Deck | Slides | Features |
|---|---|---|
| chapter25_4e | 41 | inline OMML (4, 7), pasted equation picture, missing fonts |
| Cryptography (CSC 382) | 27 | 16:9, groups, tables, JPEG backgrounds |
| ch10 | 86 | 4:3, OLE/EMF equations |
| risk assessment (CSC 382) | 22 | 14 SmartArt |
| Piracy final (own) | 10 | 4 SmartArt, native chart, builds |
| Ethics IN Computer Security (CSC 382) | 52 | builds on 6 slides |
| ethics-hidden (copy) | 52 | slide 3 hidden |
| CVpresentation (own) | 14 | embedded video (slide 13) |

## Times (open → inspect → PDF → PNGs → close)

| Deck | Cold total (incl. launch ≈0.25 s and quit) | Warm total | PDF | PNG all | PDF size (DC / Keynote) |
|---|---|---|---|---|---|
| chapter25_4e | 4.5 s | 3.4 s | 0.96 s | 0.68 s | 26 MB / 8.9 MB |
| Cryptography | 2.9 s | 2.2 s | 1.0 s | 0.8 s | **155 MB** / 3.0 MB |
| ch10 | 4.6 s | 3.6 s | 2.3 s | 1.4 s | 36 MB / 2.5 MB |
| risk assessment | 1.8 s | 1.5 s | 0.5 s | 0.4 s | 9.6 MB / 1.1 MB |
| Piracy | 2.5 s | 1.8 s | 0.8 s | 0.8 s | 18 MB / 2.9 MB |
| Ethics | 5.1 s | 4.4 s | 2.0 s | 2.3 s | 32 MB / 1.8 MB |
| CVpresentation | 2.0 s | 1.6 s | 1.0 s | 0.4 s | 8.0 MB / 1.9 MB |

The inspect step (document + every slide) takes 0.1–2 s. Keynote with the fixed script: 2.8–6.7 s warm, 10.4–14.9 s cold.

Size: DeckCraft re-encodes JPEGs as lossless Flate at full resolution (Cryptography: two 2000×1125 Flate images per page). `dpi:96` gives 37 MB and `dpi:150` gives 89 MB.

## Page counts, hidden slides, builds

- Pages = slides on every deck, builds included (one page per slide, final state).
- Hidden slide: the default PDF has 51 pages for 52 slides, so page N ≠ slide N after it. With `includeHidden:true` it has 52. Keynote (`skipped slides:true`) has 52.
- `document.inspect` reports `hidden:true` and `animations` counts that match the XML.

## Fidelity against Keynote

Mean recall of Keynote's text-layer words in DeckCraft's text layer per page: 0.99–1.0 on every deck except Piracy (0.82, where DeckCraft renders the chart labels and Keynote doesn't, and vice versa). Checked by eye:

- **Inline OMML:** rendered as math text and present in the text layer (`∆Z≪`, `f /Z`, `s =`). On slide 4 the subscript `𝜆` of `𝑃𝜆` is missing from the PDF text but present in `slide.inspect`.
- **Pasted equation picture:** an image in both.
- **OLE/EMF equations (ch10 p49):** look the same; in Keynote's text layer, not in DeckCraft's, so OCR picks them up.
- **SmartArt:** looks the same and its text is in the PDF text layer (recall 1.0 against Keynote). Not in `slide.inspect`.
- **Native chart:** DeckCraft draws it correctly, with all six categories labelled. Keynote redraws it as a 3D chart with fewer labels.
- **Video:** the poster frame is rendered.
- **Fonts:** every deck reports `missingFonts` (Calibri, Corbel, …). Substitutes look right, so it isn't a failure signal.
- **Layout art:** Piracy's conclusion slide lacks the layout's circuit decoration that Keynote shows. It's cosmetic.
- No deck reported `lostSlides` or `damagedMedia`.

## OCR on DeckCraft pages (`ocrbench`, new API, accurate, 2×)

| Deck | Per page | maxRSS | Recall vs text layer |
|---|---|---|---|
| Cryptography | 0.26 s | 250 MB | 0.988 |
| ch10 | 0.27–0.35 s | 160–175 MB | 0.997 |
| chapter25_4e | 0.33 s | 139 MB | 0.989 |

Keynote's ch10 PDF re-timed in the same session: 0.27 s/page, the same. The difference against #21's 0.23–0.25 s was machine load. DeckCraft PDFs take longer to render (3.0 s vs 0.4 s for 86 pages) and use more memory, which is negligible.

## Unattended

`run.py` was started by a LaunchAgent in `gui/501` (ppid 1, no TTY):

| Run | Screen | DeckCraft (3 decks) | Keynote ch25 |
|---|---|---|---|
| launchd, now | unlocked | ok, 0.6–3.1 s each, launch 0.72 s | ok, 14.9 s cold |
| launchd, waits for lock + 30 s | **locked** (`CGSSessionScreenIsLocked=Yes`) | ok, 0.6–2.7 s, PNGs not blank, text layer present | ok, 6.75 s |

- Both need the logged-in GUI session (Aqua). A LaunchDaemon or a logged-out Mac won't work.
- No Automation consent prompt appeared for `osascript` under launchd, because consent already existed.
- Keynote presenting: the export fails (#21). DeckCraft runs as its own `-n` instance and is unaffected.
- Not run at the literal 02:00 after a `pmset` wake; a locked GUI session is the condition that matters.
