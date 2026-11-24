import unittest
from unittest.mock import patch

from namidiff.sqeleton.abcs.database_types import (
    Integer,
    Native_UUID,
    String_FixedAlphanum,
    String_UUID,
    String_VaryingAlphanum,
    Text,
)
from namidiff.sqeleton.databases.postgresql import PostgreSQL, PostgresqlDialect
from namidiff.sqeleton.databases.redshift import Dialect as RedshiftDialect


def postgres_without_server():
    "A PostgreSQL instance. Its connection pool only connects on the first query, so no server is needed."
    return PostgreSQL(thread_count=1, host="localhost", dbname="postgres")


class TestPostgreSQLStringKeys(unittest.TestCase):
    "Collation handling for string key columns. SQL generation only, no server needed."

    def test_parse_type_keeps_collation(self):
        d = PostgresqlDialect()
        path = ("public", "t")
        self.assertEqual(
            d.parse_type(path, "c", "character varying", None, None, None, "en_US.UTF-8"),
            Text(collation="en_US.UTF-8"),
        )
        self.assertEqual(d.parse_type(path, "c", "text", None, None, None, "C"), Text(collation="C"))
        self.assertEqual(d.parse_type(path, "c", "text"), Text())
        self.assertEqual(d.parse_type(path, "c", "integer", None, 32, 0, None), Integer())

    def test_collate_key_linguistic(self):
        d = PostgresqlDialect()
        for collation in ["en_US.UTF-8", "en_US.utf8", "de_DE", "en-US-x-icu", "und-x-icu", "default", None]:
            coltype = String_VaryingAlphanum(collation=collation)
            self.assertEqual(d.collate_key('"id"', coltype), '("id" COLLATE "C")', collation)
        coltype = String_FixedAlphanum(length=8, collation="en_US.UTF-8")
        self.assertEqual(d.collate_key('"id"', coltype), '("id" COLLATE "C")')

    def test_collate_key_untouched(self):
        d = PostgresqlDialect()
        # Already in code point order: leave the column alone, so the planner can use its index
        for collation in ["C", "POSIX", "C.UTF-8", "C.utf8", "ucs_basic", "pg_c_utf8", "pg_unicode_fast"]:
            self.assertEqual(d.collate_key('"id"', String_VaryingAlphanum(collation=collation)), '"id"', collation)
        # Other key types are compared natively
        self.assertEqual(d.collate_key('"id"', String_UUID(collation="en_US.UTF-8")), '"id"')
        self.assertEqual(d.collate_key('"id"', Native_UUID()), '"id"')
        self.assertEqual(d.collate_key('"id"', Integer()), '"id"')
        self.assertEqual(d.collate_key('"id"', Text(collation="en_US.UTF-8")), '"id"')

    def test_collate_key_warns_once_per_column(self):
        d = PostgresqlDialect()
        coltype = String_VaryingAlphanum(collation="en_US.UTF-8")
        with self.assertLogs("database", level="WARNING") as logs:
            d.collate_key('"id"', coltype)
            d.collate_key('"id"', coltype)
            d.collate_key('"other"', coltype)
        self.assertEqual(len(logs.output), 2)
        self.assertIn("'en_US.UTF-8'", logs.output[0])
        self.assertIn('CREATE INDEX ON <table> ("id" COLLATE "C")', logs.output[0])
        self.assertIn('"other"', logs.output[1])

    def test_redshift_collate_key_untouched(self):
        'Redshift orders by bytes, and has no COLLATE "C"'
        d = RedshiftDialect()
        for collation in ["en_US.UTF-8", None]:
            self.assertEqual(d.collate_key('"id"', String_VaryingAlphanum(collation=collation)), '"id"')

    def _database_collation(self, pg_database_row):
        conn = postgres_without_server()
        with patch.object(PostgreSQL, "query", return_value=[(pg_database_row,)]) as query:
            name = conn._database_collation()
            self.assertEqual(conn._database_collation(), name)
        query.assert_called_once()  # Cached
        return name

    def test_database_collation(self):
        # PostgreSQL 14 and older: libc only, no datlocprovider
        self.assertEqual(self._database_collation({"datcollate": "en_US.utf8"}), "en_US.utf8")
        self.assertEqual(self._database_collation({"datcollate": "C"}), "C")
        # PostgreSQL 15, 16: daticulocale. ICU databases often keep C.UTF-8 or C in datcollate, which doesn't apply
        row = {"datlocprovider": "c", "datcollate": "en_US.UTF-8", "daticulocale": None}
        self.assertEqual(self._database_collation(row), "en_US.UTF-8")
        row = {"datlocprovider": "i", "datcollate": "C.UTF-8", "daticulocale": "en-US"}
        self.assertEqual(self._database_collation(row), "en-US-x-icu")
        # PostgreSQL 17+: datlocale, and the builtin provider
        row = {"datlocprovider": "i", "datcollate": "C", "datlocale": "und"}
        self.assertEqual(self._database_collation(row), "und-x-icu")
        row = {"datlocprovider": "b", "datcollate": "en_US.UTF-8", "datlocale": "C.UTF-8"}
        self.assertEqual(self._database_collation(row), "pg_c_utf8")
        row = {"datlocprovider": "b", "datcollate": "en_US.UTF-8", "datlocale": "C"}
        self.assertEqual(self._database_collation(row), "C")

    def test_query_table_schema_fills_in_database_collation(self):
        conn = postgres_without_server()
        rows = [
            ("id", "character varying", None, None, None, None),
            ("id2", "text", None, None, None, "C"),
            ("n", "integer", None, 32, 0, None),
        ]
        query = patch.object(PostgreSQL, "query", return_value=rows)
        database_collation = patch.object(PostgreSQL, "_database_collation", return_value="en_US.UTF-8")
        with query, database_collation:
            schema = conn.query_table_schema(("t",))
        self.assertEqual(schema["id"][5], "en_US.UTF-8")
        self.assertEqual(schema["id2"][5], "C")
        self.assertIsNone(schema["n"][5])


if __name__ == "__main__":
    unittest.main()
