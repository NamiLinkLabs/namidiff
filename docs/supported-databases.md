# List of supported databases

| Database      | Status | Connection string |
|---------------|-------------------------------------------------------------------------------------------------------------------------------------|--------|
| PostgreSQL >=10 |  💚    | `postgresql://<user>:<password>@<host>:5432/<database>`          |
| MySQL <=9.5 (9.6+ see note) |  💚    | `mysql://<user>:<password>@<hostname>:3306/<database>`             |
| Snowflake     |  💚    | `"snowflake://<user>[:<password>]@<account>/<database>/<SCHEMA>?warehouse=<WAREHOUSE>&role=<role>[&authenticator=externalbrowser]"` |
| Redshift      |  💚    | `redshift://<username>:<password>@<hostname>:5439/<database>`    |
| DuckDB >= 0.6       |  💚    | `duckdb://<file>`  |
| Trino         |  💚    | `trino://<username>:<password>@<hostname>:8080/<database>`      |
| BigQuery      |  💛    | `bigquery://<project>/<dataset>`                                |
| Oracle        |  💛    | `oracle://<username>:<password>@<hostname>:1521/<service_name>[?thick_mode=true&lib_dir=<path>]` (see note) |
| Presto        |  💛    | `presto://<username>:<password>@<hostname>:8080/<database>`     |
| Vertica       |  🪦    | `vertica://<username>:<password>@<hostname>:5433/<database>`   |
| Clickhouse    |  💛    | `clickhouse://<username>:<password>@<hostname>:9000/<database>` |
| Databricks    |  💛    | `databricks://<http_path>:<access_token>@<server_hostname>/<catalog>/<schema>`     |
| SQLite        |  📝    |                                                                                                                                     |

* 💚: Implemented and thoroughly tested.
* 💛: Implemented, but not thoroughly tested yet.
* ⏳: Implementation in progress.
* 📝: Implementation planned. Contributions welcome.
* 🪦: Unmaintained. Code is still shipped, but untested (see note below).

**Vertica is no longer maintained.** The Vertica Community Edition docker image is no longer available, so there is no way to run Vertica in CI. Its tests have been removed; the dialect remains but may break without notice.


### MySQL 9.6 and later: `MD5()` must be re-enabled

namidiff hashes rows with `MD5()`. MySQL deprecated `MD5()` and `SHA1()` in 9.4.0 and moved them out of the core server in 9.6.0. Without them, diffs fail with:

```
mysql.connector.errors.ProgrammingError: 1305 (42000): FUNCTION <db>.md5 does not exist
```

MySQL 8.0, 8.4 LTS and 9.0–9.5 are not affected. On 9.6+, a user with the required privileges (typically `root`) installs the bundled component once per server:

```sql
INSTALL COMPONENT 'file://component_classic_hashing';
```

The component is registered in the `mysql.component` table, so it survives restarts. Check it with `SELECT * FROM mysql.component;` and remove it with `UNINSTALL COMPONENT 'file://component_classic_hashing';`.

For the official Docker image, a fresh container can install it at startup by mounting an init script:

```sql
-- docker-entrypoint-initdb.d/classic_hashing.sql
INSTALL COMPONENT 'file://component_classic_hashing';
```

namidiff cannot switch to `SHA2()` instead: row checksums must match the `MD5()` computed by the database on the other side of the diff. On managed services (RDS, Aurora, Cloud SQL, Azure), check whether your provider allows installing components.

See the [MySQL Legacy Hashing Component docs](https://dev.mysql.com/doc/refman/9.7/en/legacy-hashing-component.html).

### MySQL and MariaDB: string (varchar) key columns

HASHDIFF splits the key range into segments on the namidiff side, then asks each database for `min(key)`, `max(key)` and `key >= a AND key < b`. That only works when both databases order keys the same way namidiff does. For numbers that is a given. For strings, namidiff and most databases (Snowflake, BigQuery, DuckDB, Redshift, PostgreSQL in the `C` locale) compare by code point: `'B' < 'Z9' < '_x' < 'a1'`. MySQL and MariaDB compare by the column's collation, and the defaults are case-insensitive: under `utf8mb4_0900_ai_ci` (MySQL 8), `utf8mb4_general_ci` (MySQL 5.7) or `latin1_swedish_ci` the same keys order as `'_x' < 'a1' < 'B' < 'Z9'`. The segments then contain different rows on the two sides, and the diff reports rows as missing or extra that are not, or fails with `min_key expected to be smaller than max_key`. This is why MySQL used to refuse string keys altogether (`SUPPORTS_ALPHANUMS = False`, see [erezsh/sqeleton#22](https://github.com/erezsh/sqeleton/issues/22)).

namidiff reads each key column's collation from `information_schema` and compares string keys in code point order, whatever the collation. It does so in the expressions of its own queries: nothing in the database changes, and read access is enough.

| Key column | Compared as |
|---|---|
| Binary collation (`utf8mb4_bin`, `utf8mb4_0900_bin`, `latin1_bin`, ...) or `VARBINARY` | the column as it is |
| Any other collation, MySQL 8.0.17+ | `id COLLATE utf8mb4_0900_bin` |
| Any other collation, MariaDB | `id COLLATE utf8mb4_nopad_bin` |
| Any other collation, MySQL 5.7 and 8.0 before 8.0.17 | `CAST(id AS BINARY)` |

Columns in another character set than utf8mb4 (`latin1`, `utf8mb3`, `utf16`, ...) are converted first, e.g. `CONVERT(id USING utf8mb4) COLLATE utf8mb4_0900_bin`. All of these order exactly like namidiff and the other databases, without trailing-space padding.

#### What it costs

The database cannot use the key column's index for these expressions, so every segment query scans the table. namidiff logs a warning with the exact expression, once per key column. For a sense of scale, diffing two identical 1M-row tables on one MySQL 8.0 server (4 cores, both tables in the buffer pool, `--bisection-factor 32` unless noted):

| Key column | `--threads 1` | `--threads 4` |
|---|---|---|
| `utf8mb4_bin`, so the key's index is used | 4.0s | 1.7s |
| `utf8mb4_0900_ai_ci`, compared with `COLLATE utf8mb4_0900_bin` | 18.2s | 5.0s |
| the same, with `--bisection-factor 8` | 7.2s | 2.5s |
| the same, on MySQL 5.7 (`CAST(id AS BINARY)`) | 21.0s | – |
| `utf8mb4_0900_ai_ci`, with an index on the expression (below) | 7.4s | – |

The extra time grows with the table size and with the number of segments namidiff checks. On identical tables, that is one level of `--bisection-factor` segments. Differences add a level of segments for each mismatching segment.

#### Making it faster

Without changing the column's collation:

- **Add an index on the expression** (MySQL 8.0.13+). The optimizer then uses it for the segment queries and for min/max. Use the expression from the warning, exactly as namidiff writes it:

  ```sql
  ALTER TABLE orders ADD INDEX namidiff_id ((id COLLATE utf8mb4_0900_bin));
  ```

  MySQL 5.7 has no functional indexes. MySQL 8.0.13 to 8.0.16 can index `CAST(id AS BINARY)`, which namidiff uses there, but only for keys up to 512 bytes (utf8mb4 `VARCHAR(128)`). MariaDB cannot index the expression.
- **Without any change to the database**, check fewer segments per level, and check them in parallel. A lower `--bisection-factor` (e.g. 8) means fewer table scans, at the cost of more levels when the tables differ. More `--threads` run the scans in parallel, if the server has cores to spare.

If the application can live with case-sensitive keys, giving the key column a binary collation (`ALTER TABLE orders MODIFY id VARCHAR(36) COLLATE utf8mb4_0900_bin`) also makes the regular index usable.

#### Notes

- String keys must be alphanumeric: only ` -.0-9A-Z_a-z`. Other characters (`@`, accents, ...) make the column unusable as a key, on every database. UUID keys are detected separately and compared numerically, so they work with any collation and keep their index.
- `VARBINARY` keys work. `BINARY(n)` keys don't: MySQL pads their values with `0x00` bytes, so they never match the other side.
- `utf8mb4_bin`, `latin1_bin` and the other older binary collations are `PAD SPACE`: they treat `'abc '` as equal to `'abc'`. Keys that differ only in trailing spaces (`'abc'` on one side, `'abc '` on the other) are not supported.
- JOINDIFF (both tables on the same database) joins on the column's own collation, but it splits the key range like HASHDIFF does, so the same expressions, cost and index advice apply.

### Oracle: thin and thick mode

namidiff connects to Oracle with [python-oracledb](https://python-oracledb.readthedocs.io/), the successor of `cx_Oracle`. `cx_Oracle` is no longer maintained and does not install on Python 3.12+. `pip install namidiff[oracle]` installs `oracledb`; `cx_Oracle` is not needed any more and can be uninstalled.

`oracledb` has two modes:

- **Thin mode** (default): pure Python, no Oracle client libraries needed. Works with most servers.
- **Thick mode**: loads the Oracle Instant Client. Needed for features thin mode lacks, most commonly servers that enforce Native Network Encryption. In thin mode these fail with:

  ```
  DPY-3001: Native Network Encryption and Data Integrity is only supported in python-oracledb thick mode
  ```

  See Oracle's [feature comparison](https://python-oracledb.readthedocs.io/en/latest/user_guide/appendix_a.html) for the full list.

#### Enabling thick mode

1. Download the **Basic** or **Basic Light** package of the [Oracle Instant Client](https://www.oracle.com/database/technologies/instant-client/downloads.html) for your OS. The architecture must match your Python (`python -c "import platform; print(platform.machine())"`). On Apple Silicon (arm64) use Instant Client 23ai or newer, since 19c is x86_64 only.
2. Unzip it (on macOS, mount the DMG and run its `install_ic.sh`, or copy the files) into a directory, e.g. `~/oracle/instantclient`.
3. Tell namidiff to use it, with the `lib_dir` parameter. Setting `lib_dir` turns thick mode on:

   ```sh
   namidiff "oracle://user:pass@host:1521/SERVICE?lib_dir=/Users/me/oracle/instantclient" TABLE1 \
            "snowflake://..." TABLE2
   ```

   or in a [configuration file](https://namidiff.namilink.com/how-to-use.html#how-to-use-with-a-configuration-file):

   ```toml
   [database.my_oracle]
   driver = "oracle"
   host = "host"
   port = 1521
   database = "SERVICE"
   user = "user"
   password = "pass"
   lib_dir = "/Users/me/oracle/instantclient"
   ```

On Linux, you can instead put the Instant Client on the library search path (`LD_LIBRARY_PATH`, or `ldconfig`) and pass `thick_mode=true` without `lib_dir`. On Windows, add it to `PATH`. On macOS always pass `lib_dir`: System Integrity Protection strips `DYLD_LIBRARY_PATH` when system binaries such as the shell are launched, so it often never reaches Python.

Thick mode is process-wide. It is enabled when the first Oracle connection that asks for it is created, and it cannot be enabled after a thin connection has been opened in the same process. If the client libraries cannot be loaded, the connection fails with an error that names `lib_dir`. It does not silently fall back to thin mode.

Python API users can also call `oracledb.init_oracle_client(lib_dir=...)` themselves before connecting.

#### Looking for a database not on the list?
If a database is not on the list, we'd still love to support it. [Please open an issue](https://github.com/NamiLinkLabs/namidiff/issues) to discuss it, or vote on existing requests to push them up our todo list.

We also accept pull-requests!
