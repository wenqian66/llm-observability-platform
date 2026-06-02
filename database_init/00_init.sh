#!/bin/bash
set -e

# Create both databases
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    CREATE DATABASE llm_gateway;
    CREATE DATABASE llm_metrics;
EOSQL

# Initialize user/app schema in llm_gateway
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname llm_gateway < /docker-entrypoint-initdb.d/postgres_init.sql.inc

# Initialize TimescaleDB schema in llm_metrics
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname llm_metrics < /docker-entrypoint-initdb.d/timescale_init.sql.inc
