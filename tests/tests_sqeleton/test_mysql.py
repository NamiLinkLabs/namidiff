import unittest
from typing import List
from unittest.mock import patch

from namidiff.databases.mysql import MySQL as NamidiffMySQL
from namidiff.sqeleton import connect
from namidiff.sqeleton import databases as db
from namidiff.sqeleton.abcs.database_types import (
    Timestamp,
    Text,
    Integer,
    String_UUID,
    String_VaryingAlphanum,
    String_FixedAlphanum,
)
from namidiff.sqeleton.abcs.mixins import AbstractMixin_NormalizeValue
from namidiff.sqeleton.databases.base import ConnectError
from namidiff.sqeleton.databases.mysql import Dialect, MySQL, is_binary_collation
from namidiff.sqeleton.schema import create_schema
from namidiff.sqeleton.utils import ArithAlphanumeric
from namidiff.table_segment import TableSegment
from namidiff.utils import Vector

from .common import CONN_STRINGS, get_conn


def mysql_without_server():
    "A namidiff MySQL instance. Its connection pool only connects on the first query, so no server is needed."
    return NamidiffMySQL(thread_count=1, host="localhost", database="mysql")


def dialect_with(nopad_binary_collation):
    d = Dialect()
    d.nopad_binary_collation = nopad_binary_collation
    return d


class TestMySQLDialect(unittest.TestCase):
    def test_sql_generation(self):
        d = Dialect.load_mixins(AbstractMixin_NormalizeValue)
        assert d.optimizer_hints("X") == "/*+ X */ "
        sql = d.normalize_timestamp("ts", Timestamp(precision=3, rounds=True))
        assert "datetime(3)" in sql and "datetime(6)" in sql

    def test_requires_database(self):
        with self.assertRaisesRegex(ValueError, "must specify a database"):
            MySQL(thread_count=1, host="localhost")


class TestMySQLStringKeys(unittest.TestCase):
    "Collation handling for string key columns (erezsh/sqeleton#22). SQL generation only, no server needed."

    def test_parse_type_keeps_collation(self):
        d = Dialect()
        path = ("db", "t")
        self.assertEqual(
            d.parse_type(path, "c", "varchar", None, None, None, "utf8mb4_0900_ai_ci"),
            Text(collation="utf8mb4_0900_ai_ci"),
        )
        self.assertEqual(d.parse_type(path, "c", "char", None, None, None, "latin1_bin"), Text(collation="latin1_bin"))
        # information_schema reports no collation for binary strings: they compare by bytes
        self.assertEqual(d.parse_type(path, "c", "varbinary"), Text(collation="binary"))
        self.assertEqual(d.parse_type(path, "c", "binary", None, None, None, None), Text(collation="binary"))
        # Not reported at all: unknown
        self.assertEqual(d.parse_type(path, "c", "varchar"), Text())
        self.assertEqual(d.parse_type(path, "c", "int"), Integer())

    def test_is_binary_collation(self):
        for c in ["binary", "utf8mb4_bin", "utf8mb4_0900_bin", "latin1_bin", "utf8mb3_bin", "utf8mb4_nopad_bin"]:
            assert is_binary_collation(c), c
        for c in [
            "utf8mb4_0900_ai_ci",
            "utf8mb4_0900_as_cs",
            "utf8mb4_general_ci",
            "latin1_swedish_ci",
            "latin1_general_cs",
        ]:
            assert not is_binary_collation(c), c

    def test_collate_key_nopad_binary_collation(self):
        "MySQL 8.0.17+ (utf8mb4_0900_bin) and MariaDB (utf8mb4_nopad_bin)"
        for nopad in ["utf8mb4_0900_bin", "utf8mb4_nopad_bin"]:
            d = dialect_with(nopad)
            for collation in [
                "utf8mb4_0900_ai_ci",
                "utf8mb4_0900_as_cs",
                "utf8mb4_general_ci",
                "utf8mb4_uca1400_ai_ci",
            ]:
                coltype = String_VaryingAlphanum(collation=collation)
                self.assertEqual(d.collate_key("`id`", coltype), f"`id` collate {nopad}")
            # Other character sets, and unknown ones, are converted first
            for collation in ["latin1_swedish_ci", "utf8mb3_general_ci", "utf8_general_ci", "utf16_general_ci", None]:
                coltype = String_VaryingAlphanum(collation=collation)
                self.assertEqual(d.collate_key("`id`", coltype), f"convert(`id` using utf8mb4) collate {nopad}")
            coltype = String_FixedAlphanum(length=8, collation="utf8mb4_general_ci")
            self.assertEqual(d.collate_key("`id`", coltype), f"`id` collate {nopad}")

    def test_collate_key_without_nopad_binary_collation(self):
        "MySQL 5.7 and 8.0 before 8.0.17 compare the UTF-8 bytes"
        d = dialect_with(None)
        for collation in [
            "utf8mb4_0900_ai_ci",
            "utf8mb4_general_ci",
            "utf8mb3_general_ci",
            "utf8_general_ci",
            "ascii_general_ci",
        ]:
            self.assertEqual(d.collate_key("`id`", String_VaryingAlphanum(collation=collation)), "cast(`id` as binary)")
        # The bytes of latin1, utf16, ... are not UTF-8: their order differs, and min/max wouldn't convert back to text
        for collation in ["latin1_swedish_ci", "cp1251_general_ci", "ucs2_general_ci", "utf16_unicode_ci", None]:
            self.assertEqual(
                d.collate_key("`id`", String_VaryingAlphanum(collation=collation)),
                "cast(convert(`id` using utf8mb4) as binary)",
            )

    def test_collate_key_untouched(self):
        for d in [dialect_with(None), dialect_with("utf8mb4_0900_bin")]:
            # Already in binary order: leave the column alone, so MySQL can use its index
            for collation in ["utf8mb4_bin", "utf8mb4_0900_bin", "latin1_bin", "binary"]:
                self.assertEqual(d.collate_key("`id`", String_VaryingAlphanum(collation=collation)), "`id`")
            # Other key types are compared natively
            self.assertEqual(d.collate_key("`id`", String_UUID(collation="utf8mb4_0900_ai_ci")), "`id`")
            self.assertEqual(d.collate_key("`id`", Integer()), "`id`")
            self.assertEqual(d.collate_key("`id`", Text(collation="utf8mb4_0900_ai_ci")), "`id`")

    def test_collate_key_warns_once_per_column(self):
        ci = String_VaryingAlphanum(collation="utf8mb4_0900_ai_ci")
        d = dialect_with("utf8mb4_0900_bin")
        with self.assertLogs("database", level="WARNING") as logs:
            d.collate_key("`id`", ci)
            d.collate_key("`id`", ci)
            d.collate_key("`other`", ci)
        self.assertEqual(len(logs.output), 2)
        self.assertIn("utf8mb4_0900_ai_ci", logs.output[0])
        self.assertIn("ADD INDEX ((`id` collate utf8mb4_0900_bin))", logs.output[0])
        self.assertIn("512 characters", logs.output[0])
        self.assertIn("`other`", logs.output[1])

        with self.assertLogs("database", level="WARNING") as logs:
            dialect_with(None).collate_key("`id`", ci)
        self.assertIn("ADD INDEX ((cast(`id` as binary)))", logs.output[0])
        self.assertIn("512 bytes", logs.output[0])

        with self.assertLogs("database", level="WARNING") as logs:
            dialect_with("utf8mb4_nopad_bin").collate_key("`id`", ci)
        self.assertIn("MariaDB cannot index", logs.output[0])
        self.assertNotIn("ADD INDEX", logs.output[0])


class TestMySQLNoPadDetection(unittest.TestCase):
    "MySQL.query_table_schema() finds the server's NO PAD binary collation once per connection"

    def _detect(self, database, available):
        sent = []

        def fake_query(sql, res_type=None):
            sent.append(sql)
            if "information_schema.collations" in sql:
                assert res_type == List[str]
                return available
            return [("id", "varchar", None, None, None, "utf8mb4_0900_ai_ci")]

        with patch.object(database, "query", fake_query):
            database.query_table_schema(("mysql", "t"))
            database.query_table_schema(("mysql", "t2"))
        return sent

    def test_detection(self):
        for available, expected in [
            (["utf8mb4_0900_bin"], "utf8mb4_0900_bin"),
            (["utf8mb4_nopad_bin"], "utf8mb4_nopad_bin"),
            # MariaDB 11.4.5+ lists both: its utf8mb4_0900_bin is an alias, PAD SPACE in some releases
            (["utf8mb4_nopad_bin", "utf8mb4_0900_bin"], "utf8mb4_nopad_bin"),
            ([], None),
        ]:
            database = mysql_without_server()
            class_dialect = type(database).dialect
            sent = self._detect(database, available)
            self.assertEqual(sum("information_schema.collations" in q for q in sent), 1, sent)  # once
            self.assertEqual(database.dialect.nopad_binary_collation, expected)
            # The dialect is shared by every MySQL connection; only this one may change
            self.assertIsNone(class_dialect.nopad_binary_collation)
            if expected is None:
                self.assertIs(database.dialect, class_dialect)
            else:
                self.assertIsNot(database.dialect, class_dialect)

    def test_keeps_other_per_connection_settings(self):
        database = mysql_without_server()
        database.set_timestamp_precision(3)
        self._detect(database, ["utf8mb4_0900_bin"])
        self.assertEqual(database.dialect.timestamp_precision, 3)
        self.assertEqual(database.dialect.nopad_binary_collation, "utf8mb4_0900_bin")
        database.enable_empty_string_as_null()
        self.assertEqual(database.dialect.nopad_binary_collation, "utf8mb4_0900_bin")
        self.assertTrue(database.dialect.empty_string_as_null)


class TestMySQLKeyRangeSQL(unittest.TestCase):
    "The SQL namidiff sends MySQL for string keys. Only compiled, never sent, so no server is needed."

    def _segment(self, key_type, nopad_binary_collation="utf8mb4_0900_bin"):
        database = mysql_without_server()
        database.dialect = dialect_with_mixins = type(database.dialect)()
        dialect_with_mixins.nopad_binary_collation = nopad_binary_collation
        schema = create_schema(
            database, ("mysql", "t"), {"id": key_type, "v": Text(collation="utf8mb4_0900_ai_ci")}, True
        )
        return database, TableSegment(database, ("mysql", "t"), ("id",), extra_columns=("v",), _schema=schema)

    def _sql_of(self, database, call, result):
        "Run 'call' with the database's query() replaced, and return the SQL it would have sent"
        sent = []

        def fake_query(sql_ast, res_type=None):
            sent.append(database.compile(sql_ast))
            return result

        with patch.object(database, "query", fake_query):
            call()
        (sql,) = sent
        return sql

    def _bounded(self, seg):
        return seg.new_key_bounds(Vector((ArithAlphanumeric("B"),)), Vector((ArithAlphanumeric("b"),)))

    def test_case_insensitive_collation(self):
        database, seg = self._segment(String_VaryingAlphanum(collation="utf8mb4_0900_ai_ci"))

        sql = self._sql_of(database, seg.query_key_range, ("0a", "y6"))
        self.assertIn("cast(min(`id` collate utf8mb4_0900_bin) as char)", sql)
        self.assertIn("cast(max(`id` collate utf8mb4_0900_bin) as char)", sql)

        sql = self._sql_of(database, self._bounded(seg).count_and_checksum, (1, 1))
        self.assertIn("(`id` collate utf8mb4_0900_bin >= 'B')", sql)
        self.assertIn("(`id` collate utf8mb4_0900_bin < 'b')", sql)
        # The values themselves are hashed as they are
        self.assertIn("cast(`id` as char)", sql)
        self.assertNotIn("`v` collate", sql)

    def test_latin1_without_nopad_binary_collation(self):
        database, seg = self._segment(String_VaryingAlphanum(collation="latin1_swedish_ci"), None)
        sql = self._sql_of(database, seg.query_key_range, ("0a", "y6"))
        self.assertIn("cast(min(cast(convert(`id` using utf8mb4) as binary)) as char)", sql)
        sql = self._sql_of(database, self._bounded(seg).count_and_checksum, (1, 1))
        self.assertIn("(cast(convert(`id` using utf8mb4) as binary) >= 'B')", sql)

    def test_binary_collation(self):
        database, seg = self._segment(String_VaryingAlphanum(collation="utf8mb4_bin"))

        sql = self._sql_of(database, seg.query_key_range, ("0a", "y6"))
        self.assertIn("cast(min(`id`) as char)", sql)

        sql = self._sql_of(database, self._bounded(seg).count_and_checksum, (1, 1))
        self.assertIn("(`id` >= 'B')", sql)
        self.assertIn("(`id` < 'b')", sql)
        self.assertNotIn("collate", sql)
        self.assertNotIn("as binary", sql)

    def test_integer_key_untouched(self):
        database, seg = self._segment(Integer())
        sql = self._sql_of(database, seg.query_key_range, (1, 9))
        self.assertIn("cast(min(`id`) as char)", sql)


@unittest.skipUnless(db.MySQL in CONN_STRINGS, "Requires MySQL")
class TestMySQLConnectErrors(unittest.TestCase):
    def _query(self, uri):
        conn = connect(uri, shared=False)
        try:
            conn.query("SELECT 1")
        finally:
            conn.close()

    def test_errors(self):
        with self.assertRaisesRegex(ConnectError, "Bad user name or password"):
            self._query("mysql://mysql:wrong_password@localhost/mysql")
        with self.assertRaisesRegex(ConnectError, "Database does not exist"):
            # Root (see dev/dev.env), so the server reports a missing database rather than access denied
            self._query("mysql://root:RootPassword1@localhost/no_such_db_xyz")
        with self.assertRaises(ConnectError):
            self._query("mysql://mysql:Password1@localhost:1/mysql")  # Nothing listens on port 1
