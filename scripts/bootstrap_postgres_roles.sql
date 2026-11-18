-- ============================================================
--  Secure SQL Agent - PostgreSQL role bootstrap
--  Replace :dbname and passwords before running.
-- ============================================================

-- 1. READ-ONLY ROLE
CREATE ROLE sql_agent_readonly LOGIN PASSWORD 'REPLACE_READONLY_PASSWORD';

GRANT CONNECT ON DATABASE :dbname TO sql_agent_readonly;
GRANT USAGE ON SCHEMA public TO sql_agent_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO sql_agent_readonly;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO sql_agent_readonly;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO sql_agent_readonly;

-- 2. WRITER ROLE
CREATE ROLE sql_agent_writer LOGIN PASSWORD 'REPLACE_WRITER_PASSWORD';

GRANT CONNECT ON DATABASE :dbname TO sql_agent_writer;
GRANT USAGE ON SCHEMA public TO sql_agent_writer;
GRANT SELECT, INSERT, UPDATE, DELETE
    ON ALL TABLES IN SCHEMA public TO sql_agent_writer;
GRANT USAGE, SELECT
    ON ALL SEQUENCES IN SCHEMA public TO sql_agent_writer;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sql_agent_writer;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO sql_agent_writer;

-- NEVER grant DDL (CREATE, DROP, ALTER) to either role.
-- NEVER grant superuser or role-administration privileges.
