-- Auto-generated migration (schema drift detected by watch.py)

-- Statement 1
ALTER TABLE `customers` RENAME COLUMN `name` TO `full_name`;

-- Statement 2
CREATE INDEX `idx_full_name` ON `customers` (full_name);

