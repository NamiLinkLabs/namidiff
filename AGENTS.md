# AGENTS.md — reladiff

Orientation guide for AI agents working in this repo.

## Repo layout

```
reladiff/          # Top-level Python package (public API + CLI)
  __init__.py      # connect_to_table(), diff_tables() — the public API
  __main__.py      # CLI entry point (reladiff = reladiff.__main__:main)
  databases/       # Thin re-export shims — delegates to sqeleton/databases/
  diff_tables.py   # Algorithm dispatch, DiffResultWrapper
  hashdiff_tables.py  # HashDiffer (bisection-based, cross-db)
  joindiff_tables.py  # JoinDiffer (SQL JOIN-based, same-db)
  table_segment.py    # TableSegment: a scoped view of a table
  utils.py

sqeleton/          # Database-abstraction sub-package
  abcs/
    database_types.py   # ColType hierarchy (StringType, TemporalType, …)
    mixins.py           # AbstractMixin_NormalizeValue — the normalization hook system
    compiler.py         # SQL expression compiler
  databases/
    base.py             # BaseDialect, ThreadedDatabase, Mixin_Schema
    mysql.py            # MySQL dialect + Mixin_NormalizeValue overrides
    postgresql.py       # PostgreSQL dialect
    snowflake.py        # Snowflake dialect
    duckdb.py
    bigquery.py
    oracle.py
    redshift.py
    clickhouse.py
    trino.py / presto.py / vertica.py / databricks.py / dremio.py

tests/             # unittest-based test suite
  common.py        # DB connection strings + shared helpers
  test_diff_tables.py   # Main integration tests (cross-db combinations)
  test_joindiff.py
  test_database_types.py
  test_api.py / test_cli.py / test_config.py

dev/               # Benchmarking + seeding scripts
docker-compose.yml # Spins up MySQL, Postgres, Presto, Clickhouse, etc.
```

## Two diffing algorithms

| Algorithm  | When used                        | How it works                                     |
|------------|----------------------------------|--------------------------------------------------|
| `HASHDIFF` | Cross-database (default for two  | Bisects key range; hashes rows in each segment;  |
|            | different DB instances)          | recurses into mismatching segments               |
| `JOINDIFF` | Same database                    | Issues a single SQL JOIN to find differing rows  |

`diff_tables()` auto-selects: `JOINDIFF` when both `TableSegment` objects share the same database object, `HASHDIFF` otherwise.

## Adding a new database dialect

1. Create `sqeleton/databases/<dbname>.py`. Copy `postgresql.py` as a starting point.
2. Implement:
   - `Dialect(BaseDialect, Mixin_Schema)` — set `name`, `TYPE_CLASSES`, `ROUNDS_ON_PREC_LOSS`, `quote()`, `to_string()`, `is_distinct_from()`, `random()`, `current_timestamp()`, `offset_limit()`, `explain_as_text()`, `timestamp_value()`, `uuid_value()`, `set_timezone_to_utc()`, `parse_type()`.
   - `class Mixin_MD5(AbstractMixin_MD5)` — `md5_as_int()`.
   - `class Mixin_NormalizeValue(AbstractMixin_NormalizeValue)` — `normalize_timestamp()`, `normalize_number()`. Override `normalize_text()` if the database treats empty string and NULL differently (see MySQL for an example).
   - `class TheDB(ThreadedDatabase)` — set `dialect`, `SUPPORTS_ALPHANUMS`, `CONNECT_URI_HELP`, `CONNECT_URI_PARAMS`. Implement `create_connection()`.
3. Add an entry to `sqeleton/databases/_connect.py`'s scheme-to-class map.
4. Create a matching thin shim in `reladiff/databases/<dbname>.py` that re-exports from sqeleton.
5. Add optional dependency in `pyproject.toml` and to the relevant extras.
6. Add `docker-compose.yml` service + CI entry in `.github/workflows/ci.yml`.

## The mixin / normalization system

`AbstractMixin_NormalizeValue` (in `sqeleton/abcs/mixins.py`) defines how column values are cast to a canonical string for hashing. The dispatch is:

```
normalize_value_by_type(value, coltype)
  TemporalType  → normalize_timestamp()
  FractionalType → normalize_number()
  ColType_UUID  → normalize_uuid()
  Boolean       → normalize_boolean()
  StringType    → normalize_text()   ← override this for empty-string/NULL quirks
  *             → to_string()
```

To fix cross-database string comparison bugs (e.g. MySQL `''` vs Snowflake `NULL`): override `normalize_text()` in the database's `Mixin_NormalizeValue`. MySQL's override emits `NULLIF(cast({value} as char), '')` so that empty strings hash identically to NULLs on the other side.

## Running tests

```bash
# Minimal — requires MySQL
docker-compose up -d mysql
poetry run unittest-parallel -j 8

# Targeted
poetry run python -m unittest tests.test_diff_tables.TestMySQL -f

# Individual test
poetry run python -m unittest -k test_basic_diff -f
```

Connection strings are in `tests/common.py`. Override them in `tests/local_settings.py` (not committed).

## CLI

```bash
reladiff <db1_uri> <table1> <db2_uri> <table2> [options]
# e.g.
reladiff mysql://root:pw@localhost/mydb orders snowflake://... ORDERS --key-columns id
```

Entry point: `reladiff.__main__:main` (Click-based).

## Code style

- Format with `black -l 120`.
- No type annotations required but preferred for new public API.
- Queries are built via the compiler (`sqeleton/abcs/compiler.py`) — avoid raw f-string SQL except inside dialect methods.
