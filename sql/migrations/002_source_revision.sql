-- Additive migration; no destructive rebuilds.
ALTER TABLE meta.batches ADD COLUMN IF NOT EXISTS source_revision text NOT NULL DEFAULT 'initial';
CREATE INDEX IF NOT EXISTS batches_source_partition ON meta.batches(source,partition_key);
CREATE OR REPLACE VIEW gold.latest_forecasts AS
 SELECT f.* FROM gold.forecasts f JOIN meta.model_runs mr USING(model_run_id)
 JOIN meta.runs r ON mr.run_id=r.run_id AND r.status='success'
 WHERE mr.model_run_id=(SELECT m.model_run_id FROM meta.model_runs m JOIN meta.runs rr ON rr.run_id=m.run_id
 WHERE rr.status='success' ORDER BY m.created_at DESC LIMIT 1);
