-- Optional demo dataset so you have something for the agent to reason
-- over out of the box. Run after db/schema.sql / --init.

CREATE SCHEMA IF NOT EXISTS AGENT_DEMO;

CREATE TABLE IF NOT EXISTS AGENT_DEMO.ORDERS (
    ORDER_ID     INTEGER PRIMARY KEY,
    REGION       VARCHAR(50),
    ORDER_VALUE  DECIMAL(10,2),
    IS_REFUND    BOOLEAN,
    ORDER_DATE   DATE
);

INSERT INTO AGENT_DEMO.ORDERS VALUES
    (1, 'APAC',   120.50, FALSE, DATE '2026-01-05'),
    (2, 'APAC',    45.00, TRUE,  DATE '2026-01-06'),
    (3, 'EMEA',    89.99, FALSE, DATE '2026-01-07'),
    (4, 'EMEA',   210.00, FALSE, DATE '2026-01-08'),
    (5, 'AMER',    15.75, FALSE, DATE '2026-01-09'),
    (6, 'AMER',    99.00, FALSE, DATE '2026-01-10'),
    (7, 'APAC',    60.00, FALSE, DATE '2026-01-11'),
    (8, 'EMEA',    12.00, TRUE,  DATE '2026-01-12');
