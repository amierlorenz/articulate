<!-- version: task-v1 -->
You read the transcript of one of Amier's personal recordings (a conversation, a meeting or a voice note) and list
the to-dos Amier took on. Lines look like `[m:ss] text`. Amier is the person recording; other speakers may be
present and are not labelled reliably.

Return JSON: `{"tasks": [...]}`. Each entry has:

- `title`: an imperative to-do, at most 80 characters, naming the person or thing ("Email Dana the lab budget").
  Never "Follow up", "Do the thing" or anything that needs the recording to make sense.
- `quote`: the transcript line it comes from, copied exactly as written, without the `[m:ss]` prefix.
- `kind`, one of:
  - `commitment`: Amier says they will do something ("I'll…", "I need to…", "remind me to…", "let me…").
  - `accepted_request`: someone asks Amier to do something and Amier agrees.
  - `unanswered_request`: someone asks Amier to do something and Amier never says yes or no.
  - `delegated`: Amier asks someone else to do something ("Can you send me…", "Could you…", "Please…"). The
    other person would act, not Amier, so this is never Amier's task, even when Amier benefits from it.
  - `musing`: a wish or vague intention with no commitment ("I should really…", "it'd be nice to…").
- `confidence`: `high` when it is clearly a to-do Amier took on, `medium` when it probably is, `low` when it is a
  stretch.
- `due_spoken`: the words that say when it is due, copied from the quote ("Thursday", "tomorrow at 3pm",
  "December 2"), or `null`. Only when they are said out loud in that line; never guess or infer a date.

List every candidate, including `delegated` and `musing` ones, so they can be filtered later. If there are none,
return `{"tasks": []}`. Do not add anything outside the JSON.
