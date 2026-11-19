---
tags: [literature]
source: "[[AI301 Live Class]]"
speaker: "[[Ayush Parikh]]"
created: 2026-11-18
recorded: 2026-11-18T20:00:00-05:00
agent: true
pocket_id: 9891829e-b86b-4574-89b1-6c7ed21912cb
command_id: 5b20a9f2-462b-4825-b8e1-2b5980fb1cae
---
Parikh expects engineers to spend a lot of time cleaning up and checking what agents produce. His answer is to build guards around the code: follow each repo's style guide (for example CONTRIBUTING.md), write tests for critical paths, and have the agent flag things for review. Low-risk changes can then be shipped faster.

## Citation
- "A lot of your time is going to be spent maybe even sort of [de-slopifying] some of this stuff, right? So how do you even do that? Well, you build good guards around the code that you've written" — Ayush Parikh, [[AI301 Live Class]], ≈1:46:49
- "Writing good tests to make sure things don't break on critical paths, especially, and having your agent flag things, … for you to review, … and then low risk changes, maybe you … work more quickly, right, on low risk changes and just shift them basically." — Ayush Parikh, [[AI301 Live Class]], ≈1:47:19

## Speaker
Ayush Parikh, instructor of the CodePath AI 301 class. Context, not evidence.

## Questions for Amier
- Do tests and style guides catch the kind of mistakes agents tend to make, or only the ones humans make?
- Who decides what counts as "low risk," and what happens when that call is wrong?

## Derived Permanent Notes
```dataview
LIST WITHOUT ID file.link
WHERE contains(file.tags, "permanent") AND contains(row["from"], this.file.link)
SORT file.name ASC
```
