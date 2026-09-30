"""Tests for the timestamp_precision normalization option."""
import unittest
from datetime import datetime

from namidiff import connect_to_table, diff_tables
from namidiff.sqeleton.queries import commit
from namidiff.databases import connect
from namidiff import databases as db

from .common import DiffTestCase, test_each_database_in_list

TEST_DATABASES = {db.MySQL, db.PostgreSQL, db.Oracle, db.Snowflake, db.Redshift, db.DuckDB}


@test_each_database_in_list(TEST_DATABASES)
class TestTimestampPrecision(DiffTestCase):
    shared_connection = False  # set_timestamp_precision() modifies the connection

    def setUp(self):
        # MySQL's default DATETIME has no fractional seconds
        ts_type = "DATETIME(6)" if self.db_cls is db.MySQL else datetime
        self.src_schema = self.dst_schema = {"id": int, "ts": ts_type}
        super().setUp()
        self.connection.query(
            [
                self.src_table.insert_rows(
                    [[1, datetime(2022, 1, 1, 10, 0, 0, 123999)], [2, datetime(2022, 1, 1, 10, 0, 0, 123456)]]
                ),
                self.dst_table.insert_rows(
                    [[1, datetime(2022, 1, 1, 10, 0, 0, 123000)], [2, datetime(2022, 1, 1, 10, 0, 0, 124000)]]
                ),
                commit,
            ]
        )

    def _diff(self, algorithm, **kw):
        a = connect_to_table(self.connection, self.table_src_path, "id", extra_columns=("ts",))
        b = connect_to_table(self.connection, self.table_dst_path, "id", extra_columns=("ts",))
        return sorted(diff_tables(a, b, algorithm=algorithm, **kw))

    def test_default_full_precision(self):
        for algorithm in ("hashdiff", "joindiff"):
            self.assertEqual(
                self._diff(algorithm),
                [
                    ("+", ("1", "2022-01-01 10:00:00.123000")),
                    ("+", ("2", "2022-01-01 10:00:00.124000")),
                    ("-", ("1", "2022-01-01 10:00:00.123999")),
                    ("-", ("2", "2022-01-01 10:00:00.123456")),
                ],
                algorithm,
            )

    def test_millisecond_precision(self):
        # Truncated, not rounded: .123999 == .123000
        for algorithm in ("hashdiff", "joindiff"):
            self.assertEqual(
                self._diff(algorithm, timestamp_precision=3),
                [("+", ("2", "2022-01-01 10:00:00.124")), ("-", ("2", "2022-01-01 10:00:00.123"))],
                algorithm,
            )


class TestSetTimestampPrecision(unittest.TestCase):
    def test_invalid_precision(self):
        self.assertRaises(ValueError, connect, "duckdb://:memory:", timestamp_precision=7)

    def test_unsupported_dialect(self):
        conn = connect("duckdb://:memory:")
        conn.dialect = db.clickhouse.Dialect()
        self.assertRaises(NotImplementedError, conn.set_timestamp_precision, 3)


if __name__ == "__main__":
    unittest.main()
