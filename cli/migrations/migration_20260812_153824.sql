-- Auto-generated migration (schema drift detected by watch.py)

-- Statement 1
ALTER TABLE `customers` ADD COLUMN `name` VARCHAR(100);

-- Statement 2
ALTER TABLE `customers` ADD COLUMN `test_col` VARCHAR(100);

