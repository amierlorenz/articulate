# Exam Dates and Units

Extract exam dates, times, units, and reschedule notices from the course announcement.

## Known Exams
{{known_exams}}

## Announcement
- **ID**: {{announcement_id}}
- **Posted At**: {{posted_at}}
- **Title**: {{title}}
- **Message**:
{{message}}

## Instructions
1. Identify any mentions of tests, exams, midterms, or finals.
2. For each exam mentioned:
   - `examName`: Match to one of the known exam names if possible (e.g., "Test 1", "Midterm Exam", "Final Exam"). If it references an unrecognized exam, return the name as written in the announcement.
   - `date`: In `YYYY-MM-DD` format if mentioned. If no year is specified, assume 2026. If date is not specified, return empty string.
   - `time`: In `HH:MM` (24-hour format) if mentioned (e.g., "08:00", "19:40", "13:00"). If not mentioned, return empty string.
   - `units`: List of covered unit or chapter titles / numbers mentioned (e.g. ["Work & Energy", "Impulse & momentum"]). Return empty list if none.
   - `quote`: VERBATIM substring from the announcement message supporting this date/time/reschedule. The quote must match character-for-character.
3. Every string must not contain raw control characters (tab, backspace, form feed).

## Output Schema
Return a JSON object:
{
  "exams": [
    {
      "examName": "string",
      "date": "string",
      "time": "string",
      "units": ["string"],
      "quote": "string"
    }
  ]
}
