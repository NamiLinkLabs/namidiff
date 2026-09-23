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
| Oracle        |  💛    | `oracle://<username>:<password>@<hostname>/database`            |
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

#### Looking for a database not on the list?
If a database is not on the list, we'd still love to support it. [Please open an issue](https://github.com/NamiLinkLabs/namidiff/issues) to discuss it, or vote on existing requests to push them up our todo list.

We also accept pull-requests!
