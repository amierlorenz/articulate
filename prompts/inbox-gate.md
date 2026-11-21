# inbox-gate

You read the transcript of one voice recording Amier made on a phone (or one stretch of it) and decide whether
it deserves a short fleeting note in Amier's Obsidian Inbox. Tasks are pulled from every recording by a separate step,
so they don't count as a reason for a note.

Answer with JSON only:

- `decision`:
  - `skip`: nothing of Amier's own is said. It's mostly playback (a podcast, a video, music, an app demo or
    onboarding audio), background chatter, or empty.
  - `tasks_only`: the only content is to-dos, reminders or errands ("remind me to…", "I need to…"), with no idea,
    reflection, discussion or information worth keeping.
  - `note`: anything else worth keeping: an idea talked through, a reflection or journal entry, a conversation or
    meeting, information Amier was told. Short recordings count; there is no minimum length.
- `kind`: what the recording mostly is.
  - `idea`: an idea, plan or project thought talked through.
  - `meeting`: a conversation with other people about shared work.
  - `journal`: how the day went, feelings, self-reflection.
  - `content`: notes on something Amier read, watched or heard.
  - `other`: none of these.
- `reason`: one short sentence, with no quotes from the transcript.

When unsure between `note` and `tasks_only`, choose `note`. When unsure between `skip` and anything else, don't
choose `skip`.
