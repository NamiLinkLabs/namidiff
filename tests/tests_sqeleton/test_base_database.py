"""Tests for the generic parts of sqeleton's Database/BaseDialect (namidiff/sqeleton/databases/base.py)."""

import unittest
from dataclasses import dataclass
from datetime import datetime
from typing import List

from namidiff import sqeleton
from namidiff.sqeleton import databases as db
from namidiff.sqeleton.abcs.database_types import Decimal, UnknownColType
from namidiff.sqeleton.databases.base import (
    Mixin_OptimizerHints,
    Mixin_RandomSample,
    ThreadedDatabase,
    import_helper,
)
from namidiff.sqeleton.databases.postgresql import PostgreSQL
from namidiff.sqeleton.queries import SKIP, code, commit, table, this
from namidiff.sqeleton.query_utils import drop_table

from .common import CONN_STRINGS, get_conn


class TestHelpers(unittest.TestCase):
    def test_import_helper(self):
        @import_helper("mysql", text="Install it. ")
        def import_missing():
            import nonexistent_module_xyz  # noqa

        with self.assertRaisesRegex(
            ModuleNotFoundError, r"Install it. You can install it using 'pip install namidiff\[mysql\]'"
        ):
            import_missing()

        @import_helper()
        def import_ok():
            return 1

        assert import_ok() == 1

    def test_mixins(self):
        conn = get_conn(db.DuckDB)
        t = table("t")
        assert "LIMIT 5" in conn.compile(Mixin_RandomSample().random_sample_n(t, 5))
        assert "random()" in conn.compile(Mixin_RandomSample().random_sample_ratio_approx(t, 0.5)).lower()
        assert Mixin_OptimizerHints().optimizer_hints("PARALLEL(4)") == "/*+ PARALLEL(4) */ "

    def test_parse_type(self):
        dialect = get_conn(db.DuckDB).dialect
        assert isinstance(dialect.parse_type(("t",), "c", "WEIRD_TYPE"), UnknownColType)
        assert dialect.parse_type(("t",), "c", "DECIMAL", None, None, None) == Decimal(precision=0)

        class BadDialect(type(dialect)):
            TYPE_CLASSES = {"THING": object}

        with self.assertRaisesRegex(TypeError, "returned an unknown type"):
            BadDialect().parse_type(("t",), "c", "THING")


class TestDatabaseQuery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = get_conn(db.DuckDB)
        cls.conn.query([table("bq_t", schema={"id": int, "name": str, "ts": datetime}).create(), commit])
        cls.conn.query(table("bq_t").insert_rows([(1, "a", datetime(2020, 1, 2, 3, 4, 5)), (2, "b", None)]))
        cls.conn.query([table("bq_empty", schema={"id": int, "name": str}).create(), commit])

    @classmethod
    def tearDownClass(cls):
        drop_table(cls.conn, ("bq_t",))
        drop_table(cls.conn, ("bq_empty",))

    def test_query_skip(self):
        assert self.conn.query(SKIP) is None

    def test_query_result_types(self):
        q = self.conn.query
        assert q("SELECT id FROM bq_t ORDER BY id", List[int]) == [1, 2]
        assert q("SELECT id, name FROM bq_t ORDER BY id", List[tuple]) == [(1, "a"), (2, "b")]
        assert q("SELECT id, name FROM bq_t ORDER BY id", List[dict]) == [
            {"id": 1, "name": "a"},
            {"id": 2, "name": "b"},
        ]
        assert q("SELECT 1, 'x'", tuple) == (1, "x")
        assert q("SELECT '2020-01-02 03:04:05.123456'", datetime) == datetime(2020, 1, 2, 3, 4, 5, 123000)
        assert q("SELECT sum(id) FROM bq_empty", int) is None

        @dataclass
        class Row:
            id: int
            name: str

        assert q("SELECT id, name FROM bq_t WHERE id = 1", Row) == Row(1, "a")
        assert q("SELECT id, name FROM bq_t WHERE id = 100", Row) is None

    def test_query_result_errors(self):
        q = self.conn.query
        with self.assertRaisesRegex(ValueError, "Query returned 0 rows"):
            q("SELECT id FROM bq_empty", int)
        with self.assertRaisesRegex(ValueError, "Query returned NULL"):
            q(table("bq_t").delete_rows(this.id == 100), int)  # DELETE returns no result set

    def test_schema(self):
        with self.assertRaisesRegex(RuntimeError, "does not exist, or has no columns"):
            self.conn.query_table_schema(("no_such_table",))

        raw = self.conn.query_table_schema(("bq_t",))
        col_dict = self.conn._process_table_schema(("bq_t",), raw, filter_columns=["ID", "name"])
        assert set(col_dict) == {"id", "name"}
        assert set(self.conn._process_table_schema(("bq_t",), raw)) == {"id", "name", "ts"}

        raw = self.conn.query_table_schema(("bq_empty",))
        with self.assertRaisesRegex(ValueError, "appears to be empty"):
            self.conn._process_table_schema(("bq_empty",), raw)

    def test_refine_without_normalize_mixin(self):
        # A connection created without the NormalizeValue mixin samples raw text columns
        plain = sqeleton.connect(CONN_STRINGS[db.DuckDB], shared=False)
        try:
            plain.query([table("bq_plain", schema={"id": int, "name": str}).create(), commit])
            plain.query(table("bq_plain").insert_rows([(1, "a")]))
            raw = plain.query_table_schema(("bq_plain",))
            schema, samples = plain.process_query_table_schema(("bq_plain",), raw)
            assert samples == [("a",)], samples
        finally:
            plain.close()

    def test_list_tables_and_commit(self):
        tables = self.conn.list_tables("bq_%")
        assert {t for (t,) in tables} >= {"bq_t", "bq_empty"}
        self.conn.commit()

    def test_compile(self):
        assert self.conn.compile(table("bq_t").select(this.id)) == 'SELECT "id" FROM "bq_t"'


@unittest.skipUnless(db.MySQL in CONN_STRINGS, "Requires MySQL")
class TestBaseTablePath(unittest.TestCase):
    def test_normalize_table_path(self):
        conn = get_conn(db.MySQL)  # Uses the generic Database._normalize_table_path
        assert conn._normalize_table_path(("t",)) == (conn.default_schema, "t")
        assert conn._normalize_table_path(("s", "t")) == ("s", "t")
        with self.assertRaisesRegex(ValueError, "Bad table path"):
            conn._normalize_table_path(("a", "b", "c"))
        assert conn.is_autocommit is False


class TestThreadedDatabaseInitError(unittest.TestCase):
    def test_init_error_is_raised_on_query(self):
        class BrokenDB(PostgreSQL):
            def __init__(self):
                ThreadedDatabase.__init__(self, thread_count=1)

            def create_connection(self):
                raise RuntimeError("cannot connect")

        broken = BrokenDB()
        try:
            with self.assertRaisesRegex(RuntimeError, "cannot connect"):
                broken.query("SELECT 1")
        finally:
            broken.close()
