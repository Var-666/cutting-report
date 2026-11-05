-- Keep the PDM directory location as it was when the dispatch was created.
ALTER TABLE cutting_dispatches ADD COLUMN file_location TEXT COLLATE "C";
