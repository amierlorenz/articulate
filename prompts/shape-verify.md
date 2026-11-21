# Shape Prompt Verify

You verify study prompts generated for concept "{{title}}" in course "{{course_key}}".
Check whether each prompt is pedagogically sound, unambiguous, accurate according to the concept's details, and does not leak answers.

Return JSON:
{
  "valid": true,
  "reason": "Accurate, well-grounded, and meets pedagogical criteria."
}
or if invalid:
{
  "valid": false,
  "reason": "<explanation of defect>"
}
