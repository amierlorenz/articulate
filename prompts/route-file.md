# Route File

Classify the given course file into one of three roles based on its filename, folder hint, and sample content.

## Roles
- `slides`: Presentation slide decks, lecture slides, lecture presentations.
- `guideOrExam`: Study guides, exam reviews, practice exams, sample tests, past exams, test practice questions, answer keys.
- `other`: Syllabi, readings, journal articles, textbook chapters, problem sets, homework assignments, administrative documents, spreadsheets, media files, or anything not slides or study guides/exams.

## Inputs
- **Filename**: {{filename}}
- **Folder / Source Hint**: {{folder_hint}}
- **Sample Text**:
{{page_text}}

## Instructions
1. Classify the file into exactly one of: `slides`, `guideOrExam`, `other`.
2. Do not include raw control characters (tabs, backspaces, form feeds) in your output.

## Output Schema
Return a JSON object:
{
  "role": "slides" | "guideOrExam" | "other",
  "reason": "Brief explanation"
}
