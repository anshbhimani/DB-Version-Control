-- Auto-generated migration (schema drift detected by watch.py)

-- Statement 1
ALTER TABLE `orders` DROP FOREIGN KEY `orders_ibfk_1`;

-- Statement 2
DROP TABLE `orders`;

