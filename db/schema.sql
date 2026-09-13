-- Run once against your Exasol instance (or via `python -m db.exasol_client --init`)

CREATE SCHEMA IF NOT EXISTS AGENT_REGISTRY;

-- The agent's growing, visible toolkit + audit log.
CREATE TABLE IF NOT EXISTS AGENT_REGISTRY.UDF_CATALOG (
    UDF_NAME              VARCHAR(128)  PRIMARY KEY,
    PURPOSE               VARCHAR(2000),
    SIGNATURE             VARCHAR(500),
    SOURCE_CODE           CLOB,
    TEST_REPORT           CLOB,
    CREATED_AT            TIMESTAMP,
    CREATED_FOR_QUESTION  VARCHAR(2000),
    STATUS                VARCHAR(20)   -- 'REGISTERED' | 'RETIRED'
);

-- Every question the agent has ever answered, and how (pure SQL vs. a
-- newly-minted UDF), for the trace/audit view.
CREATE TABLE IF NOT EXISTS AGENT_REGISTRY.QUESTION_LOG (
    QUESTION_ID     VARCHAR(64) PRIMARY KEY,
    QUESTION_TEXT   VARCHAR(4000),
    USED_NEW_UDF    BOOLEAN,
    UDF_NAME        VARCHAR(128),
    ANSWER_SUMMARY  VARCHAR(4000),
    ASKED_AT        TIMESTAMP
);
