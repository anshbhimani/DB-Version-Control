-- Auto-generated migration (schema drift detected by watch.py)

-- Statement 1
CREATE TABLE `orders` (
    `id` INTEGER NOT NULL,
    `customer_id` INTEGER,
    `amount` DECIMAL(10, 2),
    PRIMARY KEY (id)
);

-- Statement 2
CREATE TABLE `customers` (
    `id` INTEGER NOT NULL,
    `name` VARCHAR(50),
    PRIMARY KEY (id)
);

-- Statement 3
ALTER TABLE `orders` ADD CONSTRAINT `orders_ibfk_1` FOREIGN KEY (customer_id) REFERENCES customers(id);

