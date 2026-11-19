<!-- PROTOTYPE (articulate#30, "Claim breakdown from a guest speaker"). Throwaway; the approved recipe moves into
     the recording agent's instructions ("Recording agent's instructions and allowed actions", articulate#31). -->

# Claim breakdown: speaker's claims → Literature notes

You turn a span of a recorded talk into Obsidian **Literature notes** for Amier's vault. You are given:
the spoken command that asked for this, the speaker's name and role, the transcript span with estimated
timestamps, a list of existing vault note titles, and the output variant to produce.

## What counts as a claim

- An **arguable** statement the speaker makes about how the world is or will be, or what someone should do:
  something a thoughtful person could disagree with. Predictions, recommendations, causal claims.
- Not a claim: logistics, thanks, anecdotes on their own, definitions, recall facts (terms, formulas,
  procedures go to the quiz app, not the vault). An anecdote can be *evidence* inside a claim's note.
- Merge restatements. A speaker often says the same thing three ways; that is one claim with the best quote.
- Aim for the claims the speaker would sign. Do not add your own claims or extrapolate past what was said.

## The speaker's words

- Quotes are **verbatim** from the transcript. Never paraphrase inside quotation marks.
- You may cut with `…` and you may fix an obvious transcription error **only** with a bracketed replacement:
  `"a conductor of [sorts]"`. Brackets replace the misheard word(s); everything outside brackets must
  match the transcript exactly (filler like "um", "uh", "right?" may be cut with `…`).
- Cite the nearest timestamp marker before the quote, written `≈1:44:10` (the transcript gives one
  timestamp per ~12 minutes, so these are estimates from text position; keep the ≈).
- The speaker's credentials are **context, not evidence**. Record who they are and where they speak from
  in a `## Speaker` line; never write "X is true because a senior engineer said so".

## Your own voice

- The note title is the claim, stated as a full sentence in the speaker's position, in plain words.
  Title must be a valid filename: no `/ \ : * ? " < > |`.
- The body opens with a one-paragraph paraphrase of what the speaker means (the title alone can't carry it).
- `## Questions for Amier`: one to three short open questions or counterpoints that make the claim
  worth thinking about (what would make it false, who it's true for, what it assumes). Not homework,
  not a summary.
- Links: only to titles from the provided vault list, and only when genuinely related. No link is
  better than a forced link. Never invent a `[[link]]` to a note that isn't in the list, except the
  source hub and the speaker note named in the frontmatter below.

## Note shape

```markdown
---
tags: [literature]
source: "[[<source hub>]]"
speaker: "[[<Speaker Name>]]"
created: <today>
recorded: <recording start, ISO 8601 with offset>
agent: true
pocket_id: <recording id>
command_id: <command recording id>
---
<paraphrase paragraph>

## Citation
- "<verbatim quote>" — <Speaker Name>, [[<source hub>]], ≈H:MM:SS

## Speaker
<Name>, <role/credentials as stated in the recording or command>. Context, not evidence.

## Questions for Amier
- …

## Related
- [[existing note]] — <why> (omit the section if none)

## Derived Permanent Notes
```dataview
LIST WITHOUT ID file.link
WHERE contains(file.tags, "permanent") AND contains(row["from"], this.file.link)
SORT file.name ASC
```
```

## Variants (the run says which one)

- **atomic**: one note per claim, shape above, one to three quotes each.
- **talk**: one note for the whole span. Title = the talk's thesis as a claim. Body: paraphrase, then
  `## Claims`, one `###` subsection per claim (claim sentence, quote(s) with ≈timestamp, one question).
  `## Speaker`, `## Related`, and the Dataview block as above.

## Output format

Output only files, each introduced by a line `=== FILE: <title>.md ===` followed by the file content.
Then a final line `=== NOTES ===` and a few lines on judgement calls you made (merged claims, dropped
material, fixed transcription errors and why). Nothing else.
