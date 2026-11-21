-- Shape slice migration (§3.4)
CREATE INDEX IF NOT EXISTS idx_prompts_concept ON prompts (concept_id);
CREATE INDEX IF NOT EXISTS idx_prompts_status ON prompts (status);
