-- Runs once on first init of the postgres container.
CREATE ROLE sql_agent_readonly LOGIN PASSWORD 'readonly_pass_123';
GRANT CONNECT ON DATABASE sqlagent_demo TO sql_agent_readonly;
GRANT USAGE ON SCHEMA public TO sql_agent_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO sql_agent_readonly;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO sql_agent_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO sql_agent_readonly;
 
CREATE ROLE sql_agent_writer LOGIN PASSWORD 'writer_pass_456';
GRANT CONNECT ON DATABASE sqlagent_demo TO sql_agent_writer;
GRANT USAGE ON SCHEMA public TO sql_agent_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO sql_agent_writer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO sql_agent_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sql_agent_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO sql_agent_writer;
 
-- Defence in depth: the read-only role is read-only even outside the app.
ALTER ROLE sql_agent_readonly SET default_transaction_read_only = on;
ALTER ROLE sql_agent_readonly SET statement_timeout = '15s';
ALTER ROLE sql_agent_writer   SET statement_timeout = '15s';
