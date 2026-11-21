# Shape Definition Prompts

You write study prompts for a definition or fact concept from course "{{course_key}}".
Generate prompts matching the requested kinds according to the concept's tier.

## Rules
- flashcard:
  - "front": a clear question or prompt testing recall of the definition/fact without giving away the concept title or aliases.
  - "back": a concise, accurate answer explaining the definition/fact.
- shortAnswer:
  - "question": a prompt asking the student to explain the concept in their own words.
  - "modelAnswer": a clear, comprehensive reference answer.
  - "keyPoints": 1 to 3 distinct grading points required in a good answer.
- multipleChoice:
  - "question": a conceptual question testing understanding.
  - "options": 3 to 4 distinct options. Distractors must be plausible and relate to real course concepts. Never include the concept title or its aliases as a distractor.
  - "correctIndex": 0-based integer index of the correct option.
  - "explanation": why the correct option is right.

- Never leak the concept's title or aliases in the question or front when the student is supposed to recall or identify it.
- Ground all prompts strictly in the provided explanation and citations. Do not invent ungrounded facts.
- Do not repeat or closely paraphrase (8+ words) any style examples.

## Concept:
Title: {{title}}
Kind: {{kind}}
Tier: {{tier}}
Aliases: {{aliases}}
Explanation: {{explanation}}
Citations:
{{citations_block}}

## Style Examples:
{{style_examples_block}}

## Required Prompt Kinds:
{{required_kinds}}
