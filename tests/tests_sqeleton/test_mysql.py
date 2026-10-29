import unittest
from unittest.mock import patch

from namidiff import connect as namidiff_connect
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

from .common import CONN_STRINGS, get_conn, TEST_MYSQL_CONN_STRING


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

    def test_collate_key(self):
        d = Dialect()
        for collation in ["utf8mb4_0900_ai_ci", "utf8mb4_0900_as_cs", "utf8mb4_general_ci", "latin1_swedish_ci"]:
            self.assertEqual(d.collate_key("`id`", String_VaryingAlphanum(collation=collation)), "cast(`id` as binary)")
        self.assertEqual(
            d.collate_key("`id`", String_FixedAlphanum(length=8, collation="utf8mb4_general_ci")),
            "cast(`id` as binary)",
        )
        # Unknown collation: play safe
        self.assertEqual(d.collate_key("`id`", String_VaryingAlphanum()), "cast(`id` as binary)")
        # Character sets whose code units aren't bytes
        for collation in ["ucs2_general_ci", "utf16_unicode_ci", "utf32_general_ci"]:
            self.assertEqual(
                d.collate_key("`id`", String_VaryingAlphanum(collation=collation)),
                "cast(convert(`id` using utf8mb4) as binary)",
            )
        # Already in binary order: leave the column alone, so MySQL can use its index
        for collation in ["utf8mb4_bin", "utf8mb4_0900_bin", "latin1_bin", "binary"]:
            self.assertEqual(d.collate_key("`id`", String_VaryingAlphanum(collation=collation)), "`id`")
        # Other key types are compared natively
        self.assertEqual(d.collate_key("`id`", String_UUID(collation="utf8mb4_0900_ai_ci")), "`id`")
        self.assertEqual(d.collate_key("`id`", Integer()), "`id`")
        self.assertEqual(d.collate_key("`id`", Text(collation="utf8mb4_0900_ai_ci")), "`id`")

    def test_collate_key_warns_once_per_column(self):
        d = Dialect()
        ci = String_VaryingAlphanum(collation="utf8mb4_0900_ai_ci")
        with self.assertLogs("database", level="WARNING") as logs:
            d.collate_key("`id`", ci)
            d.collate_key("`id`", ci)
            d.collate_key("`other`", ci)
        self.assertEqual(len(logs.output), 2)
        self.assertIn("utf8mb4_0900_ai_ci", logs.output[0])
        self.assertIn("ADD INDEX ((cast(`id` as binary)))", logs.output[0])
        self.assertIn("`other`", logs.output[1])


class TestMySQLKeyRangeSQL(unittest.TestCase):
    "The SQL namidiff sends MySQL for string keys. connect() opens connections lazily, so no server is needed."

    def _segment(self, key_type):
        database = namidiff_connect(TEST_MYSQL_CONN_STRING)
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
        self.assertIn("cast(min(cast(`id` as binary)) as char)", sql)
        self.assertIn("cast(max(cast(`id` as binary)) as char)", sql)

        sql = self._sql_of(database, self._bounded(seg).count_and_checksum, (1, 1))
        self.assertIn("(cast(`id` as binary) >= 'B')", sql)
        self.assertIn("(cast(`id` as binary) < 'b')", sql)
        # The values themselves are hashed as they are
        self.assertIn("cast(`id` as char)", sql)
        self.assertNotIn("cast(`v` as binary)", sql)

    def test_binary_collation(self):
        database, seg = self._segment(String_VaryingAlphanum(collation="utf8mb4_bin"))

        sql = self._sql_of(database, seg.query_key_range, ("0a", "y6"))
        self.assertIn("cast(min(`id`) as char)", sql)

        sql = self._sql_of(database, self._bounded(seg).count_and_checksum, (1, 1))
        self.assertIn("(`id` >= 'B')", sql)
        self.assertIn("(`id` < 'b')", sql)
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
