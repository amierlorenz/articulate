-- Canvas intake slice migration (§3.4)
CREATE INDEX IF NOT EXISTS idx_source_files_slot ON source_files (course_key, slot);
CREATE INDEX IF NOT EXISTS idx_exams_course ON exams (course_key);
