-- SQL statements to transform old_schema to new_schema
-- ================================================

-- Statement 1
ALTER TABLE `address` DROP FOREIGN KEY `FK81ihijcn1kdfwffke0c0sjqeb`;

-- Statement 2
DROP TABLE address;

-- Statement 3
DROP TABLE person;

-- Statement 4
DROP TABLE user;

-- Statement 5
DROP TABLE users;

-- Statement 6
CREATE TABLE customers (
    id INTEGER NOT NULL, name VARCHAR(100), age INTEGER, category VARCHAR(20), PRIMARY KEY (id), CONSTRAINT ck_age_category CHECK ((((`age` < 13) and (`category` = _utf8mb4'child')) or ((`age` >= 13) and (`age` < 60) and (`category` = _utf8mb4'adult')) or ((`age` >= 60) and (`category` = _utf8mb4'senior'))))
);

-- Statement 7
CREATE TABLE department (
    id INTEGER NOT NULL, name VARCHAR(100), PRIMARY KEY (id)
);

-- Statement 8
CREATE TABLE projects (
    id INTEGER NOT NULL, name VARCHAR(100) NOT NULL, start_date DATE NOT NULL, end_date DATE NOT NULL, PRIMARY KEY (id), CONSTRAINT ck_date_order CHECK ((`start_date` <= `end_date`))
);

-- Statement 9
CREATE TABLE student_department (
    student_id INTEGER NOT NULL, department_id INTEGER NOT NULL, assigned_on DATE, PRIMARY KEY (student_id, department_id)
);

-- Statement 10
CREATE TABLE students (
    id INTEGER NOT NULL, name VARCHAR(100) NOT NULL, age INTEGER NOT NULL, PRIMARY KEY (id), CONSTRAINT students_chk_1 CHECK ((`age` > 18)), CONSTRAINT students_chk_2 CHECK ((`age` > 18))
);

-- Statement 11
ALTER TABLE student_department ADD CONSTRAINT student_department_ibfk_1 FOREIGN KEY (student_id) REFERENCES students(id);

-- Statement 12
ALTER TABLE student_department ADD CONSTRAINT student_department_ibfk_2 FOREIGN KEY (student_id) REFERENCES students(id);

-- Total statements: 12
