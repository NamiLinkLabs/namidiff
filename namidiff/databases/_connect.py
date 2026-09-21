import logging

from namidiff.sqeleton.databases import Connect

from .postgresql import PostgreSQL
from .mysql import MySQL
from .oracle import Oracle
from .snowflake import Snowflake
from .bigquery import BigQuery
from .redshift import Redshift
from .presto import Presto
from .databricks import Databricks
from .trino import Trino
from .clickhouse import Clickhouse
from .vertica import Vertica
from .duckdb import DuckDB


DATABASE_BY_SCHEME = {
    "postgresql": PostgreSQL,
    "mysql": MySQL,
    "oracle": Oracle,
    "redshift": Redshift,
    "snowflake": Snowflake,
    "presto": Presto,
    "bigquery": BigQuery,
    "databricks": Databricks,
    "duckdb": DuckDB,
    "trino": Trino,
    "clickhouse": Clickhouse,
    "vertica": Vertica,
}


class Connect_SetUTC(Connect):
    __doc__ = Connect.__call__.__doc__

    def __call__(self, db_conf, thread_count=1, shared=True, empty_string_as_null=False, timestamp_precision=None):
        """Connect to a database, like :meth:`namidiff.sqeleton.connect`, applying namidiff's normalization options.

        Parameters:
            db_conf (str | dict): The configuration for the database to connect. URI or dict.
            thread_count (int, optional): Size of the threadpool. Ignored by cloud databases. (default: 1)
            shared (bool): Whether to cache and return the same connection for the same db_conf. (default: True)
            empty_string_as_null (bool): Treat empty strings as NULL when normalizing text values. (default: False)
            timestamp_precision (int, optional): Normalize timestamps to this many fractional digits (1-6),
                                                 truncating the rest. (default: column precision)
        """
        db = super().__call__(db_conf, thread_count=thread_count, shared=shared)
        if empty_string_as_null:
            db.enable_empty_string_as_null()
        if timestamp_precision is not None:
            db.set_timestamp_precision(timestamp_precision)
        return db

    def _connection_created(self, db):
        db = super()._connection_created(db)
        try:
            db.query(db.dialect.set_timezone_to_utc())
        except NotImplementedError:
            logging.debug(
                f"Database '{db}' does not allow setting timezone. We recommend making sure it's set to 'UTC'."
            )
        return db


connect = Connect_SetUTC(DATABASE_BY_SCHEME)
