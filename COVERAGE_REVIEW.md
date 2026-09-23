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
