-- SQL migration script

-- Statement 1
ALTER TABLE students ADD CONSTRAINT `students_chk_1` CHECK ((`age` > 18));

-- Statement 2
ALTER TABLE students ADD CONSTRAINT `students_chk_2` CHECK ((`age` > 18));

-- Statement 3
ALTER TABLE `customers` ADD COLUMN `name` VARCHAR(100);

-- Statement 4
ALTER TABLE customers ADD CONSTRAINT `ck_age_category` CHECK ((((`age` < 13) and (`category` = _utf8mb4'child')) or ((`age` >= 13) and (`age` < 60) and (`category` = _utf8mb4'adult')) or ((`age` >= 60) and (`category` = _utf8mb4'senior'))));

-- Statement 5
ALTER TABLE projects ADD CONSTRAINT `ck_date_order` CHECK ((`start_date` <= `end_date`));

-- Statement 6
ALTER TABLE student_department ADD CONSTRAINT `student_department_ibfk_1` FOREIGN KEY (student_id) REFERENCES students(id);

-- Statement 7
ALTER TABLE student_department ADD CONSTRAINT `student_department_ibfk_2` FOREIGN KEY (student_id) REFERENCES students(id);

