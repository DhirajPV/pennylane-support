-- Runs once, when the database volume is first created, as POSTGRES_USER against
-- POSTGRES_DB. An existing volume never re-runs it: `make reset` is what does.
CREATE DATABASE pennylane_test OWNER pennylane;
