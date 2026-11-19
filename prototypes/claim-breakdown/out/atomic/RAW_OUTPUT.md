=== FILE: The future of software engineering is building automations and operating agents at a higher level.md ===
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
Parikh argues that the core of the engineering job is shifting away from writing every line of code yourself. Engineers will build automations and integrations and direct agents across projects, work streams and features. He compares this to a conductor, with each agent taking a specific task such as writing tests, a PR or a doc. He opens with a coworker merging a hundred PRs a day as a sign of where this is heading.

## Citation
- "The future of engineering and software engineering is that you are building automations, you are building integrations, you are, building at this higher level of operating your agents to work effectively for you, to work on different projects, different work streams, different features." — Ayush Parikh, [[AI301 Live Class]], ≈1:45:14
- "Where basically each agent might be picking up specific tasks. One agent might be writing tests for this one works. Another agent might be going ahead and writing a PR for a new feature." — Ayush Parikh, [[AI301 Live Class]], ≈1:45:46

## Speaker
Ayush Parikh, instructor of the CodePath AI 301 class. Context, not evidence.

## Questions for Amier
- What would have to be true about agent reliability for this to be the default way of working, and not just the way of a few early adopters?
- Does the hundred-PRs-a-day example measure anything useful, or just volume?
- Which engineering jobs does this describe, and which stay closer to hand-written code?

## Derived Permanent Notes
```dataview
LIST WITHOUT ID file.link
WHERE contains(file.tags, "permanent") AND contains(row["from"], this.file.link)
SORT file.name ASC
```

=== FILE: Engineers will need to manage several agent sessions at once with tools tailored to their own workflow.md ===
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
Parikh says that working with agents means running several at the same time, often three to five sessions. To make that workable, an engineer's tools have to be generalizable and the workflow has to fit how that person likes to work. He gives the example of an automation that pings you when an agent's PR has passed its tests, and an agent that answers existing comments on a doc before you review it.

## Citation
- "Your tools need to be so good that, and your workflow needs to be tailored to you." — Ayush Parikh, [[AI301 Live Class]], ≈1:45:46
- "you're gonna have to get used to basically managing multiple agent sessions at a time. Often three, four, five agent sessions at a time." — Ayush Parikh, [[AI301 Live Class]], ≈1:46:18

## Speaker
Ayush Parikh, instructor of the CodePath AI 301 class. Context, not evidence.

## Questions for Amier
- Is there a limit to how many parallel streams one person can keep track of before quality drops?
- How much of the "tailored workflow" is a real skill, and how much is time spent configuring tools?

## Derived Permanent Notes
```dataview
LIST WITHOUT ID file.link
WHERE contains(file.tags, "permanent") AND contains(row["from"], this.file.link)
SORT file.name ASC
```

=== FILE: Much of an engineer's time will go to reviewing agent output, so you need guards like style guides, tests and flagging.md ===
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

=== FILE: Engineering thinking does not go away, it moves up a level to decisions about parallel work, tradeoffs and design.md ===
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
Parikh says operating at a higher level is still engineering thinking, only zoomed out. The questions become how to manage different work streams and how to choose between options an agent presents, using data to prove or disprove each one. Conversations with peers shift from code to topics like latency on a path, why a technology was chosen, and whether a format generalizes.

## Citation
- "the engineering thinking is more on this zoomed out thing of how can I build stuff" — Ayush Parikh, [[AI301 Live Class]], ≈1:47:55
- "a lot of the discussions that you have with your peers, … is not gonna be about code itself." — Ayush Parikh, [[AI301 Live Class]], ≈1:47:55
- "It's more gonna be about this higher level of thinking, like, hey, how do we really optimize latency on this path?" — Ayush Parikh, [[AI301 Live Class]], ≈1:48:30

## Speaker
Ayush Parikh, instructor of the CodePath AI 301 class. Context, not evidence.

## Questions for Amier
- If peers rarely discuss code, how do newer engineers build the intuition needed to judge an agent's options?
- Does this describe all engineering teams or mainly performance-sensitive, mature codebases?

## Derived Permanent Notes
```dataview
LIST WITHOUT ID file.link
WHERE contains(file.tags, "permanent") AND contains(row["from"], this.file.link)
SORT file.name ASC
```

=== FILE: Humans are still needed to build great software products, but the nature of the role will change.md ===
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
Parikh says product thinking stays important and people remain necessary for great software products. What changes is the level at which engineers work. He frames this as a new opportunity to learn, and hopes it does not scare the students.

## Citation
- "Product thinking, … doesn't go away, right? … humans are still needed to build great software products. I don't think that's gonna change. I just think the nature of the role is gonna change" — Ayush Parikh, [[AI301 Live Class]], ≈1:49:02

## Speaker
Ayush Parikh, instructor of the CodePath AI 301 class. Context, not evidence.

## Questions for Amier
- What would "great" mean here, and which parts of product thinking could an agent take over?
- Is the changed role one most current engineers will want, or will it push some out?

## Derived Permanent Notes
```dataview
LIST WITHOUT ID file.link
WHERE contains(file.tags, "permanent") AND contains(row["from"], this.file.link)
SORT file.name ASC
```

=== FILE: Fundamentals and critical thinking still matter because agents make mistakes and unsupported decisions.md ===
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
Parikh argues that the critical thinking learned from studying computer science stays valuable. Agents make mistakes and sometimes reach conclusions without checking, such as without reading the logs, so a human has to question their decisions.

## Citation
- "I think the fundamentals still matter, guys, right? The fundamental critical thinking that you learn as somebody who has studied computer science or anything related to computer science doesn't go away." — Ayush Parikh, [[AI301 Live Class]], ≈1:49:02 and ≈1:49:32
- "You still need to think critically because agents still make make mistakes, make decisions that you're like, wait, why did you go do that thing, right? Did you really look into the logs?" — Ayush Parikh, [[AI301 Live Class]], ≈1:49:32

## Speaker
Ayush Parikh, instructor of the CodePath AI 301 class. Context, not evidence.

## Questions for Amier
- If agents improve, does the need for human critical review shrink, and what would show that it has?
- Which fundamentals matter most for catching an agent's bad conclusions?

## Derived Permanent Notes
```dataview
LIST WITHOUT ID file.link
WHERE contains(file.tags, "permanent") AND contains(row["from"], this.file.link)
SORT file.name ASC
```

=== NOTES ===
- Merged the "conductor" metaphor, the hundred-PRs example and the "higher level of operating agents" statements into the first note. The 1:45:46 agent examples go there as well.
- Split multi-agent session management from the review-and-guards material, since they make different arguable claims.
- Quote fixes: "despopifying" became "[de-slopifying]", my best guess at the misheard word. I kept "you are, building" and "make make" as spoken. I cut filler ("um", "uh") with `…`.
- The fundamentals quote spans two markers (≈1:49:02 and ≈1:49:32), so both are cited.
- I dropped the closing remarks ("happy to chat offline"), the QA logistics and the "scary" aside.
- I left out Related links because no existing vault note was a genuine match, and the working-memory note was too much of a stretch.