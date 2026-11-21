-- Add a contract version without rewriting or dropping existing facts.
ALTER TABLE meta.batches ADD COLUMN IF NOT EXISTS data_contract_version integer NOT NULL DEFAULT 1;
COMMENT ON COLUMN meta.batches.data_contract_version IS 'Version of input validation contract; additive migration on existing batches';
