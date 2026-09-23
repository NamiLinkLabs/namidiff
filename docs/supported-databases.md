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
