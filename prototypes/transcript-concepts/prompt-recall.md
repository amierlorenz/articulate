You are reading the automatic transcript of one university lecture, split into numbered windows of a few minutes each. Speech recognition makes mistakes (misheard terms, missing punctuation), and the transcript includes small talk, logistics and other students.

Course: {course}
Deck concepts already extracted from this lecture's slides (key: title):
{deck}

Your job is to pull out what a student should learn from what the instructor *says*.

1. **newConcepts**: every concept the instructor teaches that is not already in the deck list. A concept is a term with a meaning, a fact worth recalling, or a method a student applies. Include every term the instructor names and explains, every list or category they walk through (each kind in it is a concept when they explain it), and every rule, process or ranking they describe, even if covered in a minute. Lectures often wander: the teaching is mixed with stories, so look at every window. Skip small talk, logistics, announcements and anecdotes that teach nothing. If a story or example illustrates a concept, put it in that concept's `workedExamples`.
2. **spokenClaims**: for deck concepts only, what the instructor adds beyond the slides: a clearer explanation, an example, a condition. Set `correctsSlide` only if the instructor explicitly says a slide is wrong. Leave empty when the deck list is empty.
3. **spokenHints**: only explicit statements that something will or won't be tested, or that it is especially important. Copy the sentence exactly.
4. **contrastGroups**: sets of concepts (new or deck) a student could plausibly mix up, with the deciding feature the instructor states.

Rules for citations:
- Every claim cites 1 to 3 windows. The `quote` is copied **exactly** from that window's text, word for word, including misheard words and odd spelling. Keep quotes short (5 to 25 words) and inside one window.
- Write the claim `text` and the concept `title` correctly: fix obvious mistranscriptions there (for example a misheard technical term), never in the quote. If you are unsure what term was meant, lower `confidence` and say why.
- `gapFill: true` only for a claim you add from general knowledge to make an explanation complete; it has no citations. Use it rarely.
- Leave method-only fields (`aliases`, `useWhen`, `conditions`) empty for definitions and facts.

Transcript windows:
{windows}
