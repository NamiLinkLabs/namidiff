# Coverage work — notes for morning review

Branch: `fix-unit-tests`. Nothing committed.

## Result
| Scope | Before | After |
|---|---|---|
| Total (measured files) | 81% | **97%** |
| `namidiff` core (excluding sqeleton) | — | **98%** |
| `namidiff/sqeleton` | — | **97%** |

Every measured file is ≥ 90% (lowest: `sqeleton/databases/postgresql.py` at 90%). `__main__.py` went from 0% to 99%, thanks to in-process CLI tests.
Suite: 584 tests. All green after the float fix (see **A** below). Also green under `unittest-parallel -j 8`.

New test files:
- `tests/test_cli_inprocess.py`: CLI via click `CliRunner` on DuckDB files (all options, `--conf`, `-i`, errors)
- `tests/test_diff_unit.py`: HashDiffer / JoinDiffer / `diff_tables` API edge cases on in-memory DuckDB
- `tests/test_utils.py`: `namidiff.utils`, `parse_time`
- `tests/tests_sqeleton/test_connect.py`: URI/dict/TOML parsing, using fake DB classes
- `tests/tests_sqeleton/test_base_database.py`: `Database.query()` result types, schema handling, threaded init errors
- `tests/tests_sqeleton/test_query_builder.py`: query builder / compiler, executed on DuckDB
- `tests/tests_sqeleton/test_bound_schema.py`: `bound_exprs`, `schema`, `query_utils` (DuckDB, PG, MySQL, Oracle)
- `tests/tests_sqeleton/test_mysql.py`: MySQL dialect and connection errors

Extended: `tests/test_config.py` (error paths), `tests/test_joindiff.py` (`test_sample_exclusive_rows`), `tests/tests_sqeleton/test_utils.py`.

## Setup decisions
- Added `coverage` to the `dev` dependency group (`pyproject.toml` + `uv.lock`).
- Added `[tool.coverage.run]` / `[tool.coverage.report]` to `pyproject.toml`.
  - **Omitted from measurement:** `sqeleton/repl.py`, `sqeleton/conn_editor.py`, `sqeleton/__main__.py` (interactive/TUI), `namidiff/databases/*.py` (re-export shims), and dialects with no local server (snowflake, bigquery, redshift, clickhouse, trino, presto, databricks, dremio, vertica, mssql).
- Local only (not in pyproject): installed `cx_Oracle` into the `reladiff` uve env so the Oracle tests run against the `dd-oracle` container (`ORACLE_URI=oracle://oracle:Password1@localhost/app`).
- How to measure:
  ```bash
  ORACLE_URI=oracle://oracle:Password1@localhost/app coverage run -m unittest discover -s . -p "test_*.py"
  coverage report
  ```

## Source bugs found and fixed (minimal fixes, please review)
1. **`JoinDiffer(sample_exclusive_rows=True)` was completely broken** (`namidiff/joindiff_tables.py`):
   - `create_temp_table()` called `c.replace(root=False)`, but vendored sqeleton renamed the field to `_is_root` → `TypeError: Compiler.__init__() got an unexpected keyword argument 'root'`. Fixed to `_is_root=False`.
   - `self.stats["exclusive_sample"] + sample_rows` failed since `sample_rows` is a `QueryResult`, not a list → wrapped with `list(...)`.
   - Oracle: `create global temporary table ... as select` defaults to `ON COMMIT DELETE ROWS`, and the DDL commits, so the sample was always empty. Now `on commit preserve rows`, plus a `TRUNCATE` before `DROP` (otherwise ORA-14452).
   - Regression test: `tests/test_joindiff.py::test_sample_exclusive_rows` (PG, MySQL, Oracle).
2. **`--interactive` crashed on DuckDB** (`sqeleton/databases/base.py`): EXPLAIN rows were unpacked with `(row,) = row`, but DuckDB returns `(explain_key, explain_value)`. Now only 1-tuples are unpacked.
3. **`TableType` schema defaults** (`sqeleton/schema.py`): `_make_field` did `Options(default=v)`, where `v` is the column *type*, instead of the default value. Changed to `Options(default=field)`. (Feature is not used by namidiff itself.)

4. **Hashdiff empty-table shortcut typo** (`namidiff/hashdiff_tables.py:211`): `isinstance(table1, EmptyTableSegment) or isinstance(table1, EmptyTableSegment)`, where the second check should be `table2`. Only affected an optimization (an empty table2 went through `count_and_checksum`, which returns `(0, None)` anyway).
5. **`--empty-string-as-null` was silently ignored on DuckDB** (`sqeleton/databases/duckdb.py`): only MySQL, PostgreSQL and Snowflake implemented it. Added `normalize_text()` to DuckDB, mirroring PostgreSQL (`NULLIF(x::VARCHAR, '')`).
6. **Databricks URIs with query params crashed** (`sqeleton/databases/_connect.py:171`): `kw.update(dsn.query)`, but `dsn.query` is a string → `ValueError: dictionary update sequence element #0 ...`. Now parsed with `urllib.parse.parse_qsl`, like `match_path()` already does. (I can't test against real Databricks.)

7. **`Database.query(sql, SomeClass)` crashed** (`sqeleton/databases/base.py`, generic `res_type` branch): `res_type` was overwritten by its runtype canonical form, and the later `res_type(**d)` raised `TypeError: 'PythonDataType' object is not callable`. The original type is now kept for that call. (Not used by namidiff itself.)

8. **Stray `breakpoint()` in production code** (`sqeleton/queries/ast_classes.py`, `_ResolveColumn._get_resolved`): an unresolved column dropped into pdb (or hung a non-interactive run) instead of raising `QueryBuilderError`. Removed the `breakpoint()`.
9. **MySQL `Mixin_Regex` was defined but not registered** in `Dialect.MIXINS` (`sqeleton/databases/mysql.py`), so `connect.load_mixins(AbstractMixin_Regex)` couldn't give MySQL regex support. Added to `MIXINS`.

## Found but NOT fixed (need your decision)
### A. MySQL ↔ PostgreSQL float diffs — RESOLVED (option b: round everywhere)
Commit `9632fa1` truncated Float-vs-Float on MySQL and Snowflake only, while PG/DuckDB/Oracle/Redshift round, so `3.1415926` became `3.141592` vs `3.141593`.
Fix, per your decision: Floats are always rounded at the mutual precision, on every dialect.
- `hashdiff_tables.py`: dropped the `rounds=not both_float` logic.
- `mysql.py` / `snowflake.py`: dropped the `truncate(...)` branch. Float → `cast(round(x, p) as decimal(38, p))`.
- `database_types.py`: removed the `Float.rounds` field, which is now unused.
- No `precision - 1` was needed: plain rounding passes the whole float matrix.

Verified:
- `TEST_ACROSS_ALL_DBS=full -k float`: 17 failures → 0 (69 tests; MySQL/PG/Oracle/DuckDB, including mysql float↔double).
- Full suite: 584 tests OK.
- **Not verified: Snowflake.** Please run your MySQL→Snowflake DQ check. Rounding still handles float noise going in opposite directions (17.9899999 vs 17.9900001 → 17.99). The only theoretical miss is a value sitting exactly on a `…5` boundary with noise on both sides.

### B. `BoundNode.compile()` is dead/broken (`sqeleton/bound_exprs.py:38-40`)
It calls `c.compile_elem(...)`, which doesn't exist in the vendored compiler. Embedding a bound node inside another query fails. `.query()` on bound nodes works. Nothing in namidiff uses it, so it's left as is (3 uncovered lines).

### C. Minor
- `ThreadLocalInterpreter.apply_queries` uses `gen.throw(type(e), e)`, which is deprecated since Python 3.12 (warnings show in the test output). One-line fix: `gen.throw(e)`. Not changed.
- CLI: `-i` implies `debug=True` inside `_main`, but `main()` re-raises only on the original `kw["debug"]`, so errors under `-i` are logged, not raised. Not changed.
- The RTK proxy compacts `git diff` / `grep` output. Use `rtk proxy git diff` if you need a real patch.

---

# Session 2 (2026-09-23): Presto / Trino / ClickHouse, CI, MySQL MD5

The first pass never started the ClickHouse, Trino or Presto containers, and excluded those dialects from coverage as having "no local server". That was wrong: all three are in `docker-compose.yml`. They are now started, tested and measured.

## Changes (please review)
1. **Presto container didn't build.** Its base image `openjdk:11-jdk-slim-buster` was removed from Docker Hub. `dev/Dockerfile.prestosql.340` now uses `eclipse-temurin:11-jdk-jammy`, and `python-is-python3` replaces `python` (the Presto launcher needs `python`).
2. **DECISION: ClickHouse image bumped `21.12.3.32` → `22.3`** in `docker-compose.yml`. 21.12 has no arm64 build and crash-loops under emulation on Apple Silicon. 22.3 is the closest multi-arch LTS. CI (`ci.yml`) now also runs 22.3. If you'd rather keep 21.12 for CI, revert the line and use a local `docker-compose.override.yml` on Macs instead.
3. **ClickHouse: every query failed** (`Unexpected packet from server ... got Data`). This has been broken since sqeleton was vendored. `base.py` `_query_cursor` always called `c.execute(sql, args or ())`, and `clickhouse_driver` treats a tuple as INSERT data. It now passes params only when there are some. This touches every dialect; the full suite is green on all of them.
4. **Presto: every write/drop failed** with `NOT_IN_TRANSACTION`. `Presto.is_autocommit` returned `False`, so the compiler emitted `COMMIT`, but `prestodb.dbapi.connect()` defaults to autocommit. Now returns `True`, same as Trino. This fixed 179 errors.
5. **ClickHouse Bool vs other dbs.** On ClickHouse ≥22, `toString(Bool)` is `'true'`/`'false'`, while every other dialect emits `0`/`1`, so every boolean row mismatched. Added `normalize_boolean` → `toString(toUInt8(x))`.
6. `pyproject.toml`: removed clickhouse/trino/presto from the coverage `omit` list.

## CI fixes (CI-COVER-DATABASES / CI-COVER-VERSIONS)
7. **DECISION: `mysql:oracle` → `mysql:8.4` (LTS)** in `docker-compose.yml`. It fixes `1305 FUNCTION mysql.md5 does not exist`: the floating tag moved to a MySQL release without `MD5()`. This was option (1), the recommended one, from the earlier handoff. Verified: full suite (908 tests, all dbs) passes against a *fresh* `mysql:8.4` container with the compose flags.
   - ⚠️ **Local action needed:** your `mysql-data` volume was initialised by MySQL 26.7, and MySQL can't downgrade a data dir. The running `dd-mysql` is untouched, but the next time compose recreates it, it will fail. Reset it with `docker compose rm -sf mysql && docker volume rm reladiff_mysql-data && docker compose up -d mysql`. I did not do this; it deletes the volume.
8. **Trino `SERVER_STARTING_UP`:** CI ran tests before Trino finished starting. Both workflows now use `docker compose up -d --wait ...`, which blocks until the Trino image's built-in healthcheck reports healthy. **Not verified in GitHub Actions**; please check the next run.
   - Nothing needs setting up in GitHub for these jobs: MySQL, PG, Trino and ClickHouse come from docker compose. Only the Snowflake/Redshift jobs need the `SNOWFLAKE_URI` / `REDSHIFT_URI` secrets.

## Docs
9. `docs/supported-databases.md`: the MySQL row now reads "<=9.5 (9.6+ see note)". Added a section on MySQL 9.6+ (`MD5()` deprecated in 9.4.0, moved out of core in 9.6.0) covering how to re-enable it (`INSTALL COMPONENT 'file://component_classic_hashing';`), a Docker init-script variant, why namidiff can't use `SHA2()`, and a managed-services caveat. Also fixed the wrong MySQL port in the row (5432 → 3306). Sphinx build verified.

## Open items (not done)
- **D.** Optional: catch MySQL error 1305 on `md5` in the dialect and re-raise with a pointer to the docs section (option 3 from the handoff). Not done; docs only, as asked.
- **E.** Presto is still off in both CI workflows (it needs a `docker build`, which downloads the ~1 GB Presto tarball). Enable it if you want CI coverage for it.
- **F.** Local only: Trino (`-Xmx1G` in `dev/trino-conf/etc/jvm.config`) hit `OutOfMemoryError` under `unittest-parallel -j 8` with every container running. `-j 4` is fine. CI didn't show it. Config not changed.

## Session 2 result
Suite: 908 tests (was 584; the Presto/Trino/ClickHouse matrix is now included). All green with all 7 local dbs, both via `coverage run` (serial) and `unittest-parallel -j 4`.
Coverage: total 97%. New dialects: `clickhouse.py` 92%, `presto.py` 92%, `trino.py` 91%. Every measured file is still ≥90%.
Run with:
```bash
export PRESTO_URI='presto://presto@127.0.0.1:8080/postgresql/public' \
       TRINO_URI='trino://postgres@127.0.0.1:8081/postgresql/public' \
       CLICKHOUSE_URI='clickhouse://clickhouse:Password1@localhost:9000/clickhouse' \
       ORACLE_URI='oracle://oracle:Password1@localhost/app'
docker compose up -d --wait mysql postgres oracle clickhouse trino presto
coverage run -m unittest discover -s . -p "test_*.py" && coverage report
```

## Session 2, follow-up
- **CI workers `-j 16` → `-j 4`** in both workflows, so Trino (`-Xmx1G`) doesn't run out of memory (item F).
- **Presto enabled in CI** (item E): added to `docker compose up` and `PRESTO_URI` set in `ci.yml` and `ci_full.yml`. The download is a 453 MB tarball (the 934 MB figure was the built image), roughly 30–60 s per job on GitHub runners.
- `docker-compose.yml`: added a Presto `healthcheck` that runs the bundled `presto --execute "SELECT 1"` (the image removes `curl`). Without it, `--wait` can't tell when Presto has finished starting. Verified: `docker compose up -d --wait presto` → healthy.
- Verified locally with the exact `ci.yml` command (`TEST_ACROSS_ALL_DBS=0 unittest-parallel -j 4` with Trino, ClickHouse and Presto): OK. That run was against the reset `mysql:8.4` volume, so the local volume reset from item 7 is done.
- ClickHouse is still commented out in `ci_full.yml` (CI-COVER-DATABASES) but runs in `ci.yml`. Not changed.
