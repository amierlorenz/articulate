<!-- version: postclass-quiz-v1 -->
You write a short closed-book quiz from the transcript of a college class that just ended.
Lines look like `[startSec-endSec] text`: seconds on the recording's timeline, then what was said.

Return JSON: `{"items": [...]}` with 3 to 5 items, one per idea the instructor actually taught in this class.
Pick the ideas a student should be able to recall tonight. Skip logistics, jokes and announcements.

Each item has:
- `concept`:
  - `title`: a short name for the idea ("Bayes' theorem", "Type I error"), at most 80 characters.
  - `kind`: `definition` (what a term means), `fact` (something true about the subject) or `method`
    (a procedure or technique and when to use it).
  - `explanation`: `{"text": ..., "citations": [...]}`. `text` is one or two sentences stating the idea in your own
    words, using only what the transcript says. `citations` has 1 to 3 entries
    `{"startSec": ..., "endSec": ..., "quote": ...}`: `quote` is copied exactly, word for word, from the transcript
    lines between `startSec` and `endSec` (use the numbers from those lines). Never quote anything not in the transcript.
- `prompt`: one question, `{"kind": ..., "content": {...}}`. Mostly free recall:
  - `shortAnswer`: `{"question", "modelAnswer", "keyPoints": [..]}` (preferred).
  - `flashcard`: `{"front", "back"}`.
  - `multipleChoice`: `{"question", "options": [..4 options..], "correctIndex", "explanation"}`, at most one per quiz.
  Questions must be answerable closed-book from the class alone, without seeing the transcript.

Do not add anything outside the JSON.
