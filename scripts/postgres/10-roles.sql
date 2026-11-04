-- First empty-volume initialization only; psql reads private container environment.
\getenv migrator_password POSTGRES_MIGRATOR_PASSWORD
\getenv app_password POSTGRES_APP_PASSWORD
\getenv bootstrap_password POSTGRES_PASSWORD
SELECT length(:'migrator_password') > 0 AND length(:'app_password') > 0
    AND :'migrator_password' <> :'app_password'
    AND :'migrator_password' <> :'bootstrap_password'
    AND :'app_password' <> :'bootstrap_password' AS private_passwords_valid \gset
\if :private_passwords_valid
CREATE ROLE cutting_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'migrator_password';
CREATE ROLE cutting_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'app_password';
CREATE SCHEMA app_state AUTHORIZATION cutting_migrator;
GRANT USAGE ON SCHEMA app_state TO cutting_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
\else
\warn Configure three distinct nonempty private PostgreSQL passwords before first initialization.
SELECT postgres_private_password_configuration_required;
\endif
