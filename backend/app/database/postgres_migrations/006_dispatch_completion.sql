-- Completion notifications are independent of the existing dispatch state machine.
ALTER TABLE cutting_dispatches ADD COLUMN processing_finished_at TEXT COLLATE "C";
ALTER TABLE cutting_dispatches ADD COLUMN processing_finish_by TEXT COLLATE "C";
ALTER TABLE cutting_dispatches ADD COLUMN completion_received_at TEXT COLLATE "C";
CREATE INDEX idx_cutting_dispatches_task_identifier
  ON cutting_dispatches(task_identifier) WHERE task_identifier IS NOT NULL;
