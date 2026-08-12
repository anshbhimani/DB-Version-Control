-- Auto-generated migration (schema drift detected by watch.py)

-- Statement 1
ALTER TABLE `customers` RENAME COLUMN `full_name` TO `display_name`;

-- Statement 2
DROP INDEX `idx_full_name` ON `customers`;

-- Statement 3
CREATE INDEX `idx_full_name` ON `customers` (display_name);

