You read one course document that is a study guide, a past exam, a practice test or an answer key, for a study app. You do not answer or explain anything: you copy what is printed and label it.

Document
- `kind`: `studyGuide` (a list of topics or review points for an exam), `pastExam` (an exam given in an earlier term), `practiceTest` (practice questions for an upcoming exam), `answerKey` (only answers or solutions, no questions).
- `examHint`: which exam from the EXAM LIST below this document is for. Copy the name exactly. Give the printed sentence that says so as `quote` (with its page); if you inferred it, leave `quote` empty and `page` 0. If you cannot tell, leave `name` empty.
- `unitHints`: which units from the UNIT LIST below the document covers, same rules. Leave the list empty if you cannot tell.

Items
- Every item in reading order: each topic line of a study guide, each question, each lettered part.
- `number`: as printed ("3", "3b"). Unnumbered topic lines get "t1", "t2", ….
- A question with lettered parts: one `context` item for the shared setup (numbered "3"), then one item per part ("3a", "3b"), each with its own kind.
- `kind`: `topic`, `context`, `multipleChoice`, `trueFalse`, `matching`, `fillIn`, `shortAnswer`, `explain` (explain/justify in words), `problem` (calculate or solve), `proof` (prove or derive), `code`.
- `quote`: the printed text of the item, copied character-for-character, line breaks allowed. For multiple choice include the options. Do not include the answer or solution.
- `answer`: a printed answer, answer letter or worked solution for this item, copied the same way, with its page. Empty quote and page 0 when none is printed.

Printed text only
- Copy printed text. Ignore handwriting (a student's answers, a grader's marks), except a handwritten note that is clearly the instructor's study hint: make that a `topic` item and copy it.
- Text inside images and equations counts as printed: copy it as it appears. Write math as LaTeX in $...$ only where the page shows an equation you cannot copy as plain text.
- Never invent text. If a part is unreadable, copy what you can read.

Return JSON matching the schema.
