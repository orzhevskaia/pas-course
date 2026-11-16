CREATE TABLE IF NOT EXISTS meta.model_batches (
 model_run_id uuid NOT NULL REFERENCES meta.model_runs,
 batch_id bigint NOT NULL REFERENCES meta.batches,
 PRIMARY KEY(model_run_id,batch_id)
);
