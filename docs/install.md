# Installation Guide

## Install library and CLI (no drivers)

Namidiff is available on [PyPI](https://pypi.org/project/namidiff/) as **`namidiff`** (the NamiLink Kft. fork). You may install it by running:

```sh
pip install namidiff
```

Requirements: Python 3.8+ with pip.

## Install with database drivers

You may install the necessary database drivers, at the same time as when installing Namidiff, using pip's "extra" syntax.

We advise to install Namidiff within a virtual-env, because the drivers may bring many dependencies.

```sh
# Install all database drivers
pip install namidiff[all]

# The above line is equivalent to:
pip install namidiff[duckdb,mysql,postgresql,snowflake,presto,oracle,trino,clickhouse,vertica]
```

You may remove any database you don't plan to use.

For example, if you only want to diff between Postgresql and DuckDB, install Namidiff thusly:

```sh
pip install namidiff[duckdb,postgresql]
```

### Notes for shell / command-line

In some shells, like `bash` and `powershell`, you will have to use quotes, in order to allow the `[]` syntax.

For example:

```sh
pip install 'namidiff[all]'     # will work on bash
pip install "namidiff[all]"     # will work on powershell (Windows)
```

Consult your shell environment to learn the correct way to quote or escape your command.

### Notes for BigQuery

Namidiff currently doesn't auto-install the BigQuery drivers.

For BigQuery, see: [https://pypi.org/project/google-cloud-bigquery](https://pypi.org/project/google-cloud-bigquery)


### Another way to install all the drivers

For your convenience, you may also run these commands one after the other. You may omit drivers that you don't plan to use.

```bash
pip install namidiff[duckdb]
pip install namidiff[mysql]
pip install namidiff[postgresql]
pip install namidiff[snowflake]
pip install namidiff[presto]
pip install namidiff[oracle]
pip install namidiff[trino]
pip install namidiff[clickhouse]
pip install namidiff[vertica]
```
