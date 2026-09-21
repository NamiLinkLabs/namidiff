"""Unit tests for the empty_string_as_null normalization option.

No live database connections are required — only SQL generation is verified,
at the dialect level.
"""
import unittest

from namidiff.sqeleton.abcs.database_types import Text, StringType
from namidiff.sqeleton.abcs.mixins import AbstractMixin_NormalizeValue
from namidiff.databases.postgresql import PostgresqlDialect
from namidiff.databases.mysql import Dialect as MySQLDialect
from namidiff.databases.snowflake import Dialect as SnowflakeDialect
from namidiff.databases.oracle import Dialect as OracleDialect


def _normalize(dialect, sql):
    "Compile normalize_text() for the given SQL expression."
    return dialect.normalize_text(sql, Text())


class TestEmptyStringAsNullSQL(unittest.TestCase):
    def test_flag_off_passthrough(self):
        for dialect in (PostgresqlDialect(), MySQLDialect(), SnowflakeDialect(), OracleDialect()):
            sql = _normalize(dialect, "mycol")
            self.assertNotIn("NULLIF", sql)

    def test_flag_on_nullif(self):
        for dialect in (PostgresqlDialect(), MySQLDialect(), SnowflakeDialect()):
            dialect.empty_string_as_null = True
            sql = _normalize(dialect, "mycol")
            self.assertIn("NULLIF", sql)
            self.assertIn("''", sql)

    def test_flag_on_oracle_noop(self):
        "Oracle stores '' as NULL at storage time, so it doesn't override normalize_text()."
        dialect = OracleDialect()
        self.assertIs(type(dialect).normalize_text, AbstractMixin_NormalizeValue.normalize_text)


if __name__ == "__main__":
    unittest.main()
