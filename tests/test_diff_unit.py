"""Fast unit tests for the diffing core (HashDiffer, JoinDiffer, diff_tables API) on in-memory DuckDB."""

import unittest
from datetime import datetime
from unittest.mock import patch

from namidiff import Algorithm, connect, connect_to_table, diff_tables
from namidiff.hashdiff_tables import HashDiffer, _is_concat_too_long_error
from namidiff.joindiff_tables import JoinDiffer
from namidiff.query_utils import drop_table
from namidiff.sqeleton.queries import code, commit, table
from namidiff.table_segment import TableSegment

DUCKDB_URI = "duckdb://main:@:memory:"


class DuckDBTestCase(unittest.TestCase):
    tables = {}  # name -> (create_sql, rows)

    @classmethod
    def setUpClass(cls):
        cls.db = connect(DUCKDB_URI)
        for name, (cols, rows) in cls.tables.items():
            drop_table(cls.db, (name,))
            cls.db.query(f"CREATE TABLE {name} ({cols})")
            if rows:
                cls.db.query(table(name).insert_rows(rows))
        cls.db.query(commit)

    @classmethod
    def tearDownClass(cls):
        for name in cls.tables:
            drop_table(cls.db, (name,))

    def seg(self, name, *extra, key=("id",), **kw):
        return TableSegment(self.db, (name,), key, extra_columns=extra, case_sensitive=False, **kw).with_schema()


class TestHashDiffer(DuckDBTestCase):
    tables = {
        "hd_a": ("id INTEGER, ts TIMESTAMP, n INTEGER, s VARCHAR, u UUID", [(1, datetime(2020, 1, 1), 1, "a", None)]),
        "hd_b": ("id INTEGER, ts INTEGER, n VARCHAR, s INTEGER, u VARCHAR", [(1, 1, "1", 1, "x")]),
        "hd_c": ("id VARCHAR, ts TIMESTAMP", [("1", datetime(2020, 1, 1))]),
        "hd_f": ("id VARCHAR, ts TIMESTAMP", [("3a9f5d4e-8b5f-4c3e-9d0f-1a2b3c4d5e6f", datetime(2020, 1, 1))]),
        "hd_d": ("id INTEGER, s VARCHAR", [(1, "a"), (2, "b"), (3, "c")]),
        "hd_e": ("id INTEGER, s VARCHAR", [(1, "a"), (2, "B"), (4, "d")]),
        "hd_empty": ("id INTEGER, s VARCHAR", []),
        "hd_big1": ("id INTEGER, s VARCHAR", [(i, f"s{i}") for i in range(20)]),
        "hd_big2": ("id INTEGER, s VARCHAR", [(i, f"s{i}") for i in range(20) if i != 7]),
    }

    def test_param_validation(self):
        self.assertRaisesRegex(
            ValueError, "lower than threshold", HashDiffer, bisection_factor=10, bisection_threshold=5
        )
        self.assertRaisesRegex(ValueError, "at least two segments", HashDiffer, bisection_factor=1)

    def test_is_concat_too_long_error(self):
        assert _is_concat_too_long_error(Exception("ORA-01489: result of string concatenation is too long"))
        assert _is_concat_too_long_error(Exception("String is too long and would be truncated"))
        assert not _is_concat_too_long_error(Exception("some other error"))

    def test_incompatible_column_types(self):
        differ = HashDiffer()
        for col, msg in [
            ("ts", "Incompatible types for column 'ts'"),  # Temporal vs Integer
            ("n", "Incompatible types for column 'n'"),  # Integer vs Text
            ("s", "Incompatible types for column 's'"),  # Text vs Integer
            ("u", "Incompatible types for column 'u'"),
        ]:  # UUID vs Text
            with self.assertRaisesRegex(TypeError, msg):
                list(differ.diff_tables(self.seg("hd_a", col), self.seg("hd_b", col)))

    def test_incompatible_key_types(self):
        with self.assertRaisesRegex(TypeError, "Incompatible key types"):
            list(HashDiffer().diff_tables(self.seg("hd_c"), self.seg("hd_f")))  # Alphanum vs UUID

    def test_column_not_in_schema(self):
        t1 = self.seg("hd_d", "s")
        t2 = self.seg("hd_e", "s")
        t1_bad = t1.new(extra_columns=("nope",))
        with self.assertRaisesRegex(ValueError, "Column 'nope' not found in schema"):
            list(HashDiffer().diff_tables(t1_bad, t2))
        with self.assertRaisesRegex(ValueError, "Column 'nope' not found in schema"):
            list(HashDiffer().diff_tables(t2, t1_bad))

    def test_concat_too_long_retry(self):
        t1 = self.seg("hd_big1", "s")
        t2 = self.seg("hd_big2", "s")

        orig = TableSegment.count_and_checksum
        calls = []

        def flaky(self):
            calls.append(self._md5_text_columns)
            if not self._md5_text_columns:
                raise Exception("ORA-01489: result of string concatenation is too long")
            return orig(self)

        with patch.object(TableSegment, "count_and_checksum", flaky):
            diff = set(HashDiffer(bisection_threshold=3, bisection_factor=2).diff_tables(t1, t2))
        # Retried with md5-hashed text columns; results must match a normal diff
        assert True in calls and False in calls, calls
        assert diff == {("-", ("7", "s7"))}, diff

        def broken(self):
            raise RuntimeError("boom")

        with patch.object(TableSegment, "count_and_checksum", broken):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                list(HashDiffer(bisection_threshold=3, bisection_factor=2).diff_tables(t1, t2))

    def test_stats_updated_rows(self):
        res = HashDiffer().diff_tables(self.seg("hd_d", "s"), self.seg("hd_e", "s"))
        stats = res.get_stats_dict()
        assert stats["updated"] == 1 and stats["exclusive_A"] == 1 and stats["exclusive_B"] == 1, stats
        assert "rows" in res.get_stats_string()

    def test_empty_tables(self):
        t1 = self.seg("hd_d", "s")
        e = TableSegment(self.db, ("hd_empty",), ("id",), extra_columns=("s",)).with_schema(allow_empty_table=True)

        from namidiff.table_segment import EmptyTable

        with self.assertRaises(EmptyTable):
            list(HashDiffer().diff_tables(t1, e))
        with self.assertRaises(EmptyTable):
            list(HashDiffer().diff_tables(e, t1))

        diff = list(HashDiffer(allow_empty_tables=True).diff_tables(t1, e))
        assert sorted(diff) == [("-", ("1", "a")), ("-", ("2", "b")), ("-", ("3", "c"))], diff
        diff = list(HashDiffer(allow_empty_tables=True).diff_tables(e, t1))
        assert len(diff) == 3 and all(sign == "+" for sign, _ in diff), diff
        assert list(HashDiffer(allow_empty_tables=True).diff_tables(e, e)) == []

    def test_non_threaded(self):
        diff = list(HashDiffer(threaded=False).diff_tables(self.seg("hd_d", "s"), self.seg("hd_e", "s")))
        assert len(diff) == 4


class TestJoinDifferUnit(DuckDBTestCase):
    tables = {
        "jd_a": ("id INTEGER, s VARCHAR", [(1, "a"), (2, "b"), (3, "c")]),
        "jd_b": ("id INTEGER, s VARCHAR", [(1, "a"), (2, "B"), (4, "d")]),
        "jd_dup": ("id INTEGER, s VARCHAR", [(1, "a"), (1, "b")]),
        "jd_empty": ("id INTEGER, s VARCHAR", []),
    }

    def test_basic_and_stats(self):
        differ = JoinDiffer(sample_exclusive_rows=True)
        res = differ.diff_tables(self.seg("jd_a", "s"), self.seg("jd_b", "s"))
        diff = sorted(res)
        assert diff == [("+", ("2", "B")), ("+", ("4", "d")), ("-", ("2", "b")), ("-", ("3", "c"))], diff
        assert differ.stats["exclusive_count"] == 2
        assert len(differ.stats["exclusive_sample"]) == 2

    def test_different_databases_not_supported(self):
        other = connect("duckdb://main:@:memory:", shared=False)
        other.query("CREATE TABLE jd_a (id INTEGER, s VARCHAR)")
        t2 = TableSegment(other, ("jd_a",), ("id",), extra_columns=("s",)).with_schema(allow_empty_table=True)
        with self.assertRaisesRegex(ValueError, "Join-diff only works when both tables are in the same database"):
            list(JoinDiffer().diff_tables(self.seg("jd_a", "s"), t2))
        other.close()

    def test_duplicate_keys(self):
        with self.assertRaisesRegex(ValueError, "Duplicate primary keys"):
            list(JoinDiffer().diff_tables(self.seg("jd_dup", "s"), self.seg("jd_a", "s")))

    def test_empty_tables(self):
        from namidiff.table_segment import EmptyTable

        e = TableSegment(self.db, ("jd_empty",), ("id",), extra_columns=("s",)).with_schema(allow_empty_table=True)
        with self.assertRaises(EmptyTable):
            list(JoinDiffer().diff_tables(self.seg("jd_a", "s"), e))
        diff = list(JoinDiffer(allow_empty_tables=True).diff_tables(self.seg("jd_a", "s"), e))
        assert len(diff) == 3

    def test_non_threaded(self):
        diff = list(JoinDiffer(threaded=False).diff_tables(self.seg("jd_a", "s"), self.seg("jd_b", "s")))
        assert len(diff) == 4


class TestPublicApi(DuckDBTestCase):
    tables = {
        "api_a": (
            "id INTEGER, s VARCHAR, ts TIMESTAMP",
            [(1, "", datetime(2020, 1, 1, 0, 0, 0, 123456)), (2, "b", None)],
        ),
        "api_b": (
            "id INTEGER, s VARCHAR, ts TIMESTAMP",
            [(1, None, datetime(2020, 1, 1, 0, 0, 0, 123999)), (3, "c", None)],
        ),
    }

    def tearDown(self):
        # These options replace the dialect of the shared connection; restore the class default
        self.db.__dict__.pop("dialect", None)

    def test_connect_to_table_with_db_object(self):
        t = connect_to_table(self.db, "api_a", "id", empty_string_as_null=True, timestamp_precision=3)
        assert t.database is self.db
        assert t.key_columns == ("id",)
        assert self.db.dialect.timestamp_precision == 3

    def test_diff_tables_options(self):
        t1 = connect_to_table(self.db, "api_a")
        t2 = connect_to_table(self.db, "api_b")

        diff = set(diff_tables(t1, t2, key_columns="id", extra_columns=("s", "ts"), algorithm=Algorithm.HASHDIFF))
        assert len(diff) == 4, diff  # id 1 differs in s and ts; ids 2, 3 exclusive

        diff = set(
            diff_tables(
                t1,
                t2,
                key_columns="id",
                extra_columns=("s", "ts"),
                empty_string_as_null=True,
                timestamp_precision=3,
                algorithm=Algorithm.HASHDIFF,
            )
        )
        assert {k for _, (k, *_) in diff} == {"2", "3"}, diff

    def test_diff_tables_materialize_template(self):
        t1 = connect_to_table(self.db, "api_a")
        t2 = connect_to_table(self.db, "api_b")
        diff = list(diff_tables(t1, t2, algorithm=Algorithm.JOINDIFF, materialize_to_table="api_mat_%t"))
        assert len(diff) == 2
        tables = self.db.query(
            "select table_name from information_schema.tables where table_name like 'api_mat_%'", list
        )
        assert len(tables) == 1, tables
        drop_table(self.db, (tables[0][0],))

    def test_unknown_algorithm(self):
        t1 = connect_to_table(self.db, "api_a")
        with patch("namidiff.Algorithm", side_effect=lambda a: a):
            self.assertRaisesRegex(ValueError, "Unknown algorithm", diff_tables, t1, t1, algorithm="bla")
