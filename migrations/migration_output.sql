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
CREATE TABLE department (
    id INTEGER NOT NULL, name VARCHAR(100), PRIMARY KEY (id)
);

-- Statement 7
CREATE TABLE student_department (
    student_id INTEGER NOT NULL, department_id INTEGER NOT NULL, assigned_on DATE, PRIMARY KEY (student_id, department_id)
);

-- Statement 8
CREATE TABLE students (
    id INTEGER NOT NULL, name VARCHAR(100) NOT NULL, age INTEGER, PRIMARY KEY (id)
);

-- Statement 9
ALTER TABLE student_department ADD CONSTRAINT student_department_ibfk_2 FOREIGN KEY (student_id) REFERENCES students(id);

-- Statement 10
ALTER TABLE student_department ADD CONSTRAINT student_department_ibfk_1 FOREIGN KEY (student_id) REFERENCES students(id);

-- Total statements: 10
