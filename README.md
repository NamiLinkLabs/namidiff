![](reladiff_logo.svg)

&nbsp;
<br/>
<br/>
<span style="font-size:1.3em">**Namidiff**</span> is a fork of **Reladiff**, a high-performance tool and library designed for diffing large datasets across databases. By executing the diff calculation within the database itself, Namidiff minimizes data transfer and achieves optimal performance.

> **Lineage:** `namidiff` (NamiLink Kft.) is a fork of [reladiff](https://github.com/erezsh/reladiff) by Erez Shinan, which is a fork of the archived [data-diff](https://github.com/datafold/data-diff) by DataFold Inc. The PyPI distribution, Python package and CLI command are all named `namidiff`. The database layer, `namidiff.sqeleton`, is a vendored fork of [sqeleton](https://github.com/erezsh/sqeleton) by Erez Shinan. See [LICENSE](LICENSE) for the full copyright chain.

This tool is specifically tailored for data professionals, DevOps engineers, and system administrators.

Namidiff is free, open-source, user-friendly, extensively tested, and delivers fast results, even at massive scale.

### Key Features:

 1. **Cross-Database Diff**: Namidiff employs a divide-and-conquer algorithm, based on matching hashes, to efficiently identify modified segments and download only the necessary data for comparison. This approach ensures exceptional performance when differences are minimal.

    - ⇄  Diffs across over a dozen different databases (e.g. *PostgreSQL* -> *Snowflake*) !

    - 🧠 Gracefully handles reduced precision (e.g., timestamp(9) -> timestamp(3)) by rounding according to the database specification.

    - 🔥 Benchmarked to diff over 25M rows in under 10 seconds and over 1B rows in approximately 5 minutes, given no differences.

    - ♾️ Capable of handling tables with tens of billions of rows.


2. **Intra-Database Diff**: When both tables reside in the same database, Namidiff compares them using a join operation, with additional optimizations for enhanced speed.

    - Supports materializing the diff into a local table.
    - Can collect various extra statistics about the tables.

3. **Threaded**: Utilizes multiple threads to significantly boost performance during diffing operations.

3. **Configurable**: Offers numerous options for power-users to customize and optimize their usage.

4. **Automation-Friendly**: Outputs both JSON and git-like diffs (with + and -), facilitating easy integration into CI/CD pipelines.

5. **Over a dozen databases supported**. MySQL, Postgres, Snowflake, Bigquery, Oracle, Clickhouse, and more. [See full list](docs/supported-databases.md)


Reladiff is a fork of an archived project called [data-diff](https://github.com/datafold/data-diff). Namidiff continues that lineage, with NamiLink-specific fixes for cross-database normalization (e.g. empty-string / NULL handling, timestamp precision).

## Get Started

[**🗎 Read the Documentation**](https://namidiff.namilink.com/) - everything you need to start diffing. (Upstream reladiff docs are also hosted at [reladiff.readthedocs.io](https://reladiff.readthedocs.io/en/latest/).)

## Quickstart

For the impatient ;)

### Install

Namidiff is available on [PyPI](https://pypi.org/project/namidiff/) as **`namidiff`** (the NamiLink Kft. fork). You may install it by running:

```
pip install namidiff
```

Requires Python 3.8+ with pip.

We advise to install it within a virtual-env.

### How to Use

Once you've installed Namidiff, you can run it from the command-line:

```bash
# Cross-DB diff, using hashes
namidiff  DB1_URI  TABLE1_NAME  DB2_URI  TABLE2_NAME  [OPTIONS]
```

When both tables belong to the same database, a shorter syntax is available:

```bash
# Same-DB diff, using outer join
namidiff  DB1_URI  TABLE1_NAME  TABLE2_NAME  [OPTIONS]
```

Or, you can import and run it from Python:

```python
from namidiff import connect_to_table, diff_tables

table1 = connect_to_table("postgresql:///", "table_name", "id")
table2 = connect_to_table("mysql:///", "table_name", "id")

sign: Literal['+' | '-']
row: tuple[str, ...]
for sign, row in diff_tables(table1, table2):
    print(sign, row)
```

Read our detailed instructions:

* [How to use from the shell / command-line](docs/how-to-use.md#how-to-use-from-the-shell-or-command-line)
    * [How to use with TOML configuration file](docs/how-to-use.md#how-to-use-with-a-configuration-file)
* [How to use from Python](docs/how-to-use.md#how-to-use-from-python)


#### "Real-world" example: Diff "events" table between Postgres and Snowflake

```
namidiff \
  postgresql:/// \
  events \
  "snowflake://<username>:<password>@<host>/<DATABASE>/<SCHEMA>?warehouse=<WAREHOUSE>&role=<ROLE>" \
  events \
  -k event_id \         # Identifier of event
  -c event_data \       # Extra column to compare
  -w "event_time < '2024-10-10'"    # Filter the rows on both dbs
```

#### "Real-world" example: Diff "events" and "old_events" tables in the same Postgres DB

Materializes the results into a new table, containing the current timestamp in its name.

```
namidiff \
  postgresql:///  events  old_events \
  -k org_id \
  -c created_at -c is_internal \
  -w "org_id != 1 and org_id < 2000" \
  -m test_results_%t \
  --materialize-all-rows \
  --table-write-limit 10000
```

### Technical Explanation

This [technical explanation](docs/technical-explanation.md) provides a quick overview of how our cross-database diffing works.

For an in-depth explanation, this [blog post](https://eshsoft.com/blog/how-reladiff-works) details all the major technical choices we made in our implementation.


### We're here to help!

* Confused? Got a cool idea? Just want to share your thoughts? Let's discuss it in [GitHub Discussions](https://github.com/NamiLinkLabs/namidiff/discussions).

* Did you encounter a bug? [Open an issue](https://github.com/NamiLinkLabs/namidiff/issues).

## How to Contribute
* Please read the [contributing guidelines](CONTRIBUTING.md) to get started.
* Feel free to open a new issue or work on an existing one.

Big thanks to everyone who contributed so far:

<a href="https://github.com/NamiLinkLabs/namidiff/graphs/contributors">
  <img src="https://contributors-img.web.app/image?repo=NamiLinkLabs/namidiff" />
</a>

And to the upstream [reladiff](https://github.com/erezsh/reladiff) and [data-diff](https://github.com/datafold/data-diff) contributors, whose work this fork builds on.


## License

This project is licensed under the terms of the [MIT License](LICENSE).

Copyright 2026 NamiLink Kft. Contains code copyright Erez Shinan (reladiff, sqeleton) and DataFold Inc. (data-diff), also under MIT. The [LICENSE](LICENSE) file retains all original notices.
