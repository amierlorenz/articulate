# Shape Method Prompts

You write study prompts for a method concept from course "{{course_key}}".
Generate prompts matching the requested kinds according to the method's tier and context.

## Rules
- methodPick:
  - "statement": a concrete problem scenario or data description where this method applies. Do NOT name the method or any of its aliases in the statement.
  - "targetConceptId": the concept ID of this method (e.g. "{{concept_id}}").
  - "explanation": why this method is the appropriate choice over its contrast sibling methods.
  - "verifiedSiblingIds": list of sibling concept IDs from the contrast group that serve as alternative choices.
- setUp:
  - "statement": a problem scenario describing an experiment or data set.
  - "targetConceptId": "{{concept_id}}".
  - "modelSetUp": the correct formulation/setup (e.g., state null and alternative hypotheses, identify variables, formula to apply).
  - "keyPoints": 1 to 3 key elements required in a correct setup.
- shortAnswer (full solve):
  - "question": a complete exercise or problem requiring the student to apply and solve using this method.
  - "modelAnswer": complete step-by-step solution.
  - "keyPoints": 2 to 4 key grading criteria.
- multipleChoice (when requested):
  - "question": a problem scenario asking which method or formula applies.
  - "options": 3 to 4 distinct options.
  - "correctIndex": 0-based index of the correct option.
  - "explanation": why the correct option applies.

- Never leak the method's title or aliases in the statement or question where the user is asked to choose or identify the method.
- Ground all problem scenarios in the provided conditions, use-when cues, and worked examples.
- Do not repeat or closely paraphrase (8+ words) any style examples.

## Method:
Id: {{concept_id}}
Title: {{title}}
Tier: {{tier}}
Aliases: {{aliases}}
Explanation: {{explanation}}
Use When: {{use_when_block}}
Conditions: {{conditions_block}}
Worked Examples: {{worked_examples_block}}
Contrast Group: {{group_name}}
Sibling Concepts: {{sibling_ids}}

## Style Examples:
{{style_examples_block}}

## Required Prompt Kinds:
{{required_kinds}}
