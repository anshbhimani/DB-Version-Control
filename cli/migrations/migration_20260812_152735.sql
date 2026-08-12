-- Auto-generated migration (schema drift detected by watch.py)

-- Statement 1
ALTER TABLE `orders` MODIFY COLUMN `id` INTEGER NOT NULL;

-- Statement 2
ALTER TABLE `orders` MODIFY COLUMN `customer_id` INTEGER;

-- Statement 3
ALTER TABLE `orders` MODIFY COLUMN `amount` DECIMAL(10, 2);

-- Statement 4
ALTER TABLE `ai_test` MODIFY COLUMN `note` VARCHAR(50);

-- Statement 5
ALTER TABLE `ai_test` MODIFY COLUMN `id` INTEGER NOT NULL;

-- Statement 6
ALTER TABLE `customers` MODIFY COLUMN `id` INTEGER NOT NULL;

-- Statement 7
ALTER TABLE `customers` MODIFY COLUMN `email` VARCHAR(100);

-- Statement 8
ALTER TABLE `customers` MODIFY COLUMN `display_name` VARCHAR(50);

