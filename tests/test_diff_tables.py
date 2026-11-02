from datetime import datetime, timedelta
from typing import Callable
import uuid
import unittest

from parameterized import parameterized

from namidiff.sqeleton.abcs.database_types import String_VaryingAlphanum
from namidiff.sqeleton.databases.mysql import NOPAD_BINARY_COLLATIONS
from namidiff.sqeleton.queries import table, this, commit
from namidiff.sqeleton.utils import ArithAlphanumeric, numberToAlphanum

from namidiff.hashdiff_tables import HashDiffer, compare_element, diff_sets
from namidiff.joindiff_tables import JoinDiffer
from namidiff.query_utils import drop_table
from namidiff.table_segment import TableSegment, split_space, Vector
from namidiff import connect, databases as db

from .common import (
    N_THREADS,
    str_to_checksum,
    test_each_database_in_list,
    DiffTestCase,
    table_segment,
    CONN_STRINGS,
    get_conn,
    random_table_suffix,
)


TEST_DATABASES = {
    db.MySQL,
    db.PostgreSQL,
    db.Oracle,
    db.Redshift,
    db.Snowflake,
    db.BigQuery,
    db.Presto,
    db.Trino,
}

test_each_database: Callable = test_each_database_in_list(TEST_DATABASES)


class TestUtils(unittest.TestCase):
    def test_split_space(self):
        for i in range(0, 10):
            for j in range(1, 16328, 17):
                for n in range(1, 32):
                    r = split_space(i, j + i + n, n)
                    assert len(r) == n, f"split_space({i}, {j+n}, {n}) = {(r)}"


@test_each_database
class TestDates(DiffTestCase):
    src_schema = {"id": int, "datetime": datetime, "text_comment": str}

    def setUp(self):
        super().setUp()

        src_table = self.src_table
        self.now = now = datetime.now()

        rows = [
            (now - timedelta(days=50), "50 days ago"),
            (now - timedelta(hours=3), "3 hours ago"),
            (now - timedelta(minutes=10), "10 mins ago"),
            (now - timedelta(seconds=1), "1 second ago"),
            (now, "now"),
        ]

        self.connection.query(
            [
                src_table.insert_rows((i, ts, s) for i, (ts, s) in enumerate(rows)),
                table(self.table_dst_path).create(src_table),
                commit,
                src_table.insert_row(len(rows), self.now - timedelta(seconds=5), "5 seconds ago"),
                commit,
            ]
        )

    def test_init(self):
        a = table_segment(
            self.connection, self.table_src_path, "id", "datetime", max_update=self.now, case_sensitive=False
        )
        self.assertRaises(ValueError, table_segment, self.connection, self.table_src_path, "id", max_update=self.now)

    def test_basic(self):
        differ = HashDiffer(bisection_factor=10, bisection_threshold=100)
        a = table_segment(self.connection, self.table_src_path, "id", "datetime", case_sensitive=False)
        b = table_segment(self.connection, self.table_dst_path, "id", "datetime", case_sensitive=False)
        assert a.count() == 6
        assert b.count() == 5

        assert not list(differ.diff_tables(a, a))
        self.assertEqual(len(list(differ.diff_tables(a, b))), 1)

    def test_offset(self):
        differ = HashDiffer(bisection_factor=2, bisection_threshold=10)
        sec1 = self.now - timedelta(seconds=3)
        a = table_segment(self.connection, self.table_src_path, "id", "datetime", max_update=sec1, case_sensitive=False)
        b = table_segment(self.connection, self.table_dst_path, "id", "datetime", max_update=sec1, case_sensitive=False)
        assert a.count() == 4, a.count()
        assert b.count() == 3

        assert not list(differ.diff_tables(a, a))
        self.assertEqual(len(list(differ.diff_tables(a, b))), 1)

        a = table_segment(self.connection, self.table_src_path, "id", "datetime", min_update=sec1, case_sensitive=False)
        b = table_segment(self.connection, self.table_dst_path, "id", "datetime", min_update=sec1, case_sensitive=False)
        assert a.count() == 2
        assert b.count() == 2
        assert not list(differ.diff_tables(a, b))

        day1 = self.now - timedelta(days=1)

        a = table_segment(
            self.connection,
            self.table_src_path,
            "id",
            "datetime",
            min_update=day1,
            max_update=sec1,
            case_sensitive=False,
        )
        b = table_segment(
            self.connection,
            self.table_dst_path,
            "id",
            "datetime",
            min_update=day1,
            max_update=sec1,
            case_sensitive=False,
        )
        assert a.count() == 3
        assert b.count() == 2
        assert not list(differ.diff_tables(a, a))
        self.assertEqual(len(list(differ.diff_tables(a, b))), 1)


@test_each_database
class TestDiffTables(DiffTestCase):
    src_schema = {"id": int, "userid": int, "movieid": int, "rating": float, "timestamp": datetime}
    dst_schema = {"id": int, "userid": int, "movieid": int, "rating": float, "timestamp": datetime}

    def setUp(self):
        super().setUp()

        self.table = table_segment(self.connection, self.table_src_path, "id", "timestamp", case_sensitive=False)
        self.table2 = table_segment(self.connection, self.table_dst_path, "id", "timestamp", case_sensitive=False)

        self.differ = HashDiffer(bisection_factor=3, bisection_threshold=4)

    def test_properties_on_empty_table(self):
        table = self.table.with_schema()
        self.assertEqual(0, table.count())
        self.assertEqual(None, table.count_and_checksum()[1])

    def test_get_values(self):
        time = "2022-01-01 00:00:00.000000"
        time_obj = datetime.fromisoformat(time)

        cols = "id userid movieid rating timestamp".split()
        id_ = self.connection.query(
            [self.src_table.insert_row(1, 1, 1, 9, time_obj, columns=cols), commit, self.src_table.select(this.id)], int
        )

        table = self.table.with_schema()

        self.assertEqual(1, table.count())
        concatted = str(id_) + "|" + time
        self.assertEqual(str_to_checksum(concatted), table.count_and_checksum()[1])

    def test_diff_small_tables(self):
        time = "2022-01-01 00:00:00"
        time_obj = datetime.fromisoformat(time)

        cols = "id userid movieid rating timestamp".split()
        self.connection.query(
            [
                self.src_table.insert_rows([[1, 1, 1, 9, time_obj], [2, 2, 2, 9, time_obj]], columns=cols),
                self.dst_table.insert_rows([[1, 1, 1, 9, time_obj]], columns=cols),
                commit,
            ]
        )

        diff_res = self.differ.diff_tables(self.table, self.table2)
        info = diff_res.info_tree.info
        diff = list(diff_res)

        expected = [("-", ("2", time + ".000000"))]
        self.assertEqual(expected, diff)
        self.assertEqual(2, info.rowcounts[1])
        self.assertEqual(1, info.rowcounts[2])

    def test_non_threaded(self):
        differ = HashDiffer(bisection_factor=3, bisection_threshold=4, threaded=False)

        time = "2022-01-01 00:00:00"
        time_obj = datetime.fromisoformat(time)
        cols = "id userid movieid rating timestamp".split()
        self.connection.query(
            [
                self.src_table.insert_row(1, 1, 1, 9, time_obj, columns=cols),
                self.dst_table.insert_row(1, 1, 1, 9, time_obj, columns=cols),
                commit,
            ]
        )

        diff = list(differ.diff_tables(self.table, self.table2))
        self.assertEqual(diff, [])

    def test_diff_table_above_bisection_threshold(self):
        time = "2022-01-01 00:00:00"
        time_obj = datetime.fromisoformat(time)

        cols = "id userid movieid rating timestamp".split()

        self.connection.query(
            [
                self.src_table.insert_rows(
                    [
                        [1, 1, 1, 9, time_obj],
                        [2, 2, 2, 9, time_obj],
                        [3, 3, 3, 9, time_obj],
                        [4, 4, 4, 9, time_obj],
                        [5, 5, 5, 9, time_obj],
                    ],
                    columns=cols,
                ),
                self.dst_table.insert_rows(
                    [
                        [1, 1, 1, 9, time_obj],
                        [2, 2, 2, 9, time_obj],
                        [3, 3, 3, 9, time_obj],
                        [4, 4, 4, 9, time_obj],
                    ],
                    columns=cols,
                ),
                commit,
            ]
        )

        diff_res = self.differ.diff_tables(self.table, self.table2)
        info = diff_res.info_tree.info
        diff = list(diff_res)

        expected = [("-", ("5", time + ".000000"))]
        self.assertEqual(expected, diff)
        self.assertEqual(5, info.rowcounts[1])
        self.assertEqual(4, info.rowcounts[2])

    def test_return_empty_array_when_same(self):
        time = "2022-01-01 00:00:00"
        time_obj = datetime.fromisoformat(time)

        cols = "id userid movieid rating timestamp".split()

        self.connection.query(
            [
                self.src_table.insert_row(1, 1, 1, 9, time_obj, columns=cols),
                self.dst_table.insert_row(1, 1, 1, 9, time_obj, columns=cols),
                commit,
            ]
        )

        diff = list(self.differ.diff_tables(self.table, self.table2))
        self.assertEqual([], diff)

    def test_diff_sorted_by_key(self):
        time = "2022-01-01 00:00:00"
        time2 = "2021-01-01 00:00:00"

        time_obj = datetime.fromisoformat(time)
        time_obj2 = datetime.fromisoformat(time2)

        cols = "id userid movieid rating timestamp".split()

        self.connection.query(
            [
                self.src_table.insert_rows(
                    [
                        [1, 1, 1, 9, time_obj],
                        [2, 2, 2, 9, time_obj2],
                        [3, 3, 3, 9, time_obj],
                        [4, 4, 4, 9, time_obj2],
                        [5, 5, 5, 9, time_obj],
                    ],
                    columns=cols,
                ),
                self.dst_table.insert_rows(
                    [
                        [1, 1, 1, 9, time_obj],
                        [2, 2, 2, 9, time_obj],
                        [3, 3, 3, 9, time_obj],
                        [4, 4, 4, 9, time_obj],
                        [5, 5, 5, 9, time_obj],
                    ],
                    columns=cols,
                ),
                commit,
            ]
        )

        differ = HashDiffer(bisection_factor=2)
        diff = set(differ.diff_tables(self.table, self.table2))
        expected = {
            ("-", ("2", time2 + ".000000")),
            ("+", ("2", time + ".000000")),
            ("-", ("4", time2 + ".000000")),
            ("+", ("4", time + ".000000")),
        }
        self.assertEqual(expected, diff)


@test_each_database
class TestDiffTables2(DiffTestCase):
    src_schema = {"id": int, "rating": float, "timestamp": datetime}
    dst_schema = {"id2": int, "rating2": float, "timestamp2": datetime}

    def test_diff_column_names(self):
        time = "2022-01-01 00:00:00"
        time2 = "2021-01-01 00:00:00"

        time_obj = datetime.fromisoformat(time)
        time_obj2 = datetime.fromisoformat(time2)

        self.connection.query(
            [
                self.src_table.insert_rows(
                    [
                        [1, 9, time_obj],
                        [2, 9, time_obj2],
                        [3, 9, time_obj],
                        [4, 9, time_obj2],
                        [5, 9, time_obj],
                    ],
                    columns=["id", "rating", "timestamp"],
                ),
                self.dst_table.insert_rows(
                    [
                        [1, 9, time_obj],
                        [2, 9, time_obj2],
                        [3, 9, time_obj],
                        [4, 9, time_obj2],
                        [5, 9, time_obj],
                    ],
                    columns=["id2", "rating2", "timestamp2"],
                ),
            ]
        )

        table1 = table_segment(self.connection, self.table_src_path, "id", "timestamp", case_sensitive=False)
        table2 = table_segment(self.connection, self.table_dst_path, "id2", "timestamp2", case_sensitive=False)

        differ = HashDiffer(bisection_factor=2)
        diff = list(differ.diff_tables(table1, table2))
        assert diff == []


@test_each_database
class TestUUIDs(DiffTestCase):
    src_schema = {"id": str, "text_comment": str}

    def setUp(self):
        super().setUp()

        src_table = self.src_table

        self.new_uuid = str(uuid.uuid1(32132131))

        self.connection.query(
            [
                src_table.insert_rows((str(uuid.uuid1(i)), str(i)) for i in range(100)),
                table(self.table_dst_path).create(src_table),
                src_table.insert_row(self.new_uuid, "This one is different"),
                commit,
            ]
        )

        self.a = table_segment(
            self.connection, self.table_src_path, "id", extra_columns=("text_comment",), case_sensitive=False
        ).with_schema()
        self.b = table_segment(
            self.connection, self.table_dst_path, "id", extra_columns=("text_comment",), case_sensitive=False
        ).with_schema()

    def test_string_keys(self):
        differ = HashDiffer(bisection_factor=2)
        diff = list(differ.diff_tables(self.a, self.b))
        self.assertEqual(diff, [("-", (str(self.new_uuid), "This one is different"))])

        # At this point the tables should already have a schema, to ensure the column is detected as UUID and not alpanum
        self.connection.query(self.src_table.insert_row("unexpected", "<-- this bad value should not break us"))
        self.assertRaises(ValueError, list, differ.diff_tables(self.a, self.b))

    def test_where_sampling(self):
        a = self.a.replace(where="1=1")

        differ = HashDiffer(bisection_factor=2)
        diff = list(differ.diff_tables(a, self.b))
        self.assertEqual(diff, [("-", (str(self.new_uuid), "This one is different"))])

        a_empty = self.a.replace(where="1=0")
        self.assertRaises(ValueError, list, differ.diff_tables(a_empty, self.b))


@test_each_database
class TestAlphanumericKeys(DiffTestCase):
    src_schema = {"id": str, "text_comment": str}

    def setUp(self):
        super().setUp()

        src_table = self.src_table
        self.new_alphanum = "aBcDeFgHiz"

        values = []
        for i in range(0, 10000, 1000):
            a = ArithAlphanumeric(numberToAlphanum(i), max_len=10)
            if not a and isinstance(self.connection, db.Oracle):
                # Skip empty string, because Oracle treats it as NULL ..
                continue

            values.append((str(a), str(i)))

        queries = [
            src_table.insert_rows(values),
            table(self.table_dst_path).create(src_table),
            src_table.insert_row(self.new_alphanum, "This one is different"),
            commit,
        ]

        for query in queries:
            self.connection.query(query, None)

        self.a = table_segment(self.connection, self.table_src_path, "id", "text_comment", case_sensitive=False)
        self.b = table_segment(self.connection, self.table_dst_path, "id", "text_comment", case_sensitive=False)

    def test_alphanum_keys(self):
        differ = HashDiffer(bisection_factor=2, bisection_threshold=3)
        diff = list(differ.diff_tables(self.a, self.b))
        self.assertEqual(diff, [("-", (str(self.new_alphanum), "This one is different"))])

        self.connection.query([self.src_table.insert_row("@@@", "<-- this bad value should not break us"), commit])

        self.a = table_segment(self.connection, self.table_src_path, "id", "text_comment", case_sensitive=False)
        self.b = table_segment(self.connection, self.table_dst_path, "id", "text_comment", case_sensitive=False)

        self.assertRaises(NotImplementedError, list, differ.diff_tables(self.a, self.b))


@test_each_database
class TestVaryingAlphanumericKeys(DiffTestCase):
    src_schema = {"id": str, "text_comment": str}

    def setUp(self):
        super().setUp()

        src_table = self.src_table

        values = []
        for i in range(0, 10000, 1000):
            a = ArithAlphanumeric(numberToAlphanum(i * i))
            if not a and isinstance(self.connection, db.Oracle):
                # Skip empty string, because Oracle treats it as NULL ..
                continue

            values.append((str(a), str(i)))

        self.new_alphanum = "aBcDeFgHiJ"

        queries = [
            src_table.insert_rows(values),
            table(self.table_dst_path).create(src_table),
            src_table.insert_row(self.new_alphanum, "This one is different"),
            commit,
        ]

        self.connection.query(queries)

        self.a = table_segment(self.connection, self.table_src_path, "id", "text_comment", case_sensitive=False)
        self.b = table_segment(self.connection, self.table_dst_path, "id", "text_comment", case_sensitive=False)

    def test_varying_alphanum_keys(self):
        # Test the class itself
        values = ["---", "0123", "Z9", "ZZ", "_", "a", "a-", "a123", "a_"]
        alphanums = [ArithAlphanumeric(v) for v in values]
        alphanums.sort()
        self.assertEqual(values, [str(i) for i in alphanums])

        for a in alphanums:
            assert a - a == 0

        differ = HashDiffer(bisection_factor=2)
        diff = list(differ.diff_tables(self.a, self.b))
        self.assertEqual(diff, [("-", (str(self.new_alphanum), "This one is different"))])

        self.connection.query(
            [
                self.src_table.insert_row("@@@", "<-- this bad value should not break us"),
                commit,
            ]
        )

        self.a = table_segment(self.connection, self.table_src_path, "id", "text_comment", case_sensitive=False)
        self.b = table_segment(self.connection, self.table_dst_path, "id", "text_comment", case_sensitive=False)

        self.assertRaises(NotImplementedError, list, differ.diff_tables(self.a, self.b))


@test_each_database
class TestTableSegment(DiffTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.table = table_segment(self.connection, self.table_src_path, "id", "timestamp", case_sensitive=False)
        self.table2 = table_segment(self.connection, self.table_dst_path, "id", "timestamp", case_sensitive=False)

    def test_table_segment(self):
        early = datetime(2021, 1, 1, 0, 0)
        late = datetime(2022, 1, 1, 0, 0)
        self.assertRaises(ValueError, self.table.replace, min_update=late, max_update=early)

        self.assertRaises(ValueError, self.table.replace, min_key=Vector((10,)), max_key=Vector((0,)))

    def test_case_awareness(self):
        src_table = table(self.table_src_path, schema={"id": int, "userid": int, "timestamp": datetime})

        cols = "id userid timestamp".split()
        time = "2022-01-01 00:00:00.000000"
        time_obj = datetime.fromisoformat(time)

        self.connection.query(
            [src_table.create(), src_table.insert_rows([[1, 9, time_obj], [2, 2, time_obj]], columns=cols), commit]
        )

        res = tuple(self.table.replace(key_columns=("Id",), case_sensitive=False).with_schema().query_key_range())
        self.assertEqual(res, (("1",), ("2",)))

        self.assertRaises(
            KeyError, self.table.replace(key_columns=("Id",), case_sensitive=True).with_schema().query_key_range
        )


@test_each_database
class TestTableUUID(DiffTestCase):
    src_schema = {"id": str, "text_comment": str}

    def setUp(self):
        super().setUp()

        src_table = self.src_table

        values = []
        for i in range(10):
            uuid_value = str(uuid.uuid1(i))
            values.append((uuid_value, uuid_value))

        self.null_uuid = str(uuid.uuid1(32132131))

        self.connection.query(
            [
                src_table.insert_rows(values),
                table(self.table_dst_path).create(src_table),
                src_table.insert_row(self.null_uuid, None),
                commit,
            ]
        )

        self.a = table_segment(self.connection, self.table_src_path, "id", "text_comment", case_sensitive=False)
        self.b = table_segment(self.connection, self.table_dst_path, "id", "text_comment", case_sensitive=False)

    def test_uuid_column_with_nulls(self):
        differ = HashDiffer(bisection_factor=2)
        diff = list(differ.diff_tables(self.a, self.b))
        self.assertEqual(diff, [("-", (str(self.null_uuid), None))])


@test_each_database
class TestTableNullRowChecksum(DiffTestCase):
    src_schema = {"id": str, "text_comment": str}

    def setUp(self):
        super().setUp()

        src_table = self.src_table

        self.null_uuid = str(uuid.uuid1(1))
        self.connection.query(
            [
                src_table.insert_row(str(uuid.uuid1(1)), "1"),
                table(self.table_dst_path).create(src_table),
                src_table.insert_row(self.null_uuid, None),  # Add a row where a column has NULL value
                commit,
            ]
        )

        self.a = table_segment(self.connection, self.table_src_path, "id", "text_comment", case_sensitive=False)
        self.b = table_segment(self.connection, self.table_dst_path, "id", "text_comment", case_sensitive=False)

    def test_uuid_columns_with_nulls(self):
        """
        Here we test a case when in one segment one or more columns has only null values. For example,
        Table A:
        | id   |   value   |
        |------|-----------|
        | pk_1 | 'value_1' |
        | pk_2 |    NULL   |

        Table B:
        | id   |   value   |
        |------|-----------|
        | pk_1 | 'value_1' |

        We can choose some bisection factor and bisection threshold (2 and 3 for our example, respectively)
        that one segment will look like ('pk_2', NULL). Some databases, when we do a cast these values to string and
        try to concatenate, some databases return NULL when concatenating (for example, MySQL). As the result, all next
        operations like substring, sum etc return nulls that leads incorrect diff results: ('pk_2', null) should be in
        diff results, but it's not. This test helps to detect such cases.
        """

        differ = HashDiffer(bisection_factor=2, bisection_threshold=3)
        diff = list(differ.diff_tables(self.a, self.b))
        self.assertEqual(diff, [("-", (str(self.null_uuid), None))])


@test_each_database
class TestConcatMultipleColumnWithNulls(DiffTestCase):
    src_schema = {"id": str, "c1": str, "c2": str}
    dst_schema = {"id": str, "c1": str, "c2": str}

    def setUp(self):
        super().setUp()

        src_values = []
        dst_values = []

        self.diffs = []
        for i in range(0, 8):
            pk = str(uuid.uuid1(i))
            src_row = (str(pk), str(i), None)
            dst_row = (str(pk), str(i) + "-different", None)

            src_values.append(src_row)
            dst_values.append(dst_row)

            self.diffs.append(("-", src_row))
            self.diffs.append(("+", dst_row))

        self.connection.query(
            [
                self.src_table.insert_rows(src_values),
                self.dst_table.insert_rows(dst_values),
                commit,
            ]
        )

        self.a = table_segment(
            self.connection, self.table_src_path, "id", extra_columns=("c1", "c2"), case_sensitive=False
        )
        self.b = table_segment(
            self.connection, self.table_dst_path, "id", extra_columns=("c1", "c2"), case_sensitive=False
        )

    def test_tables_are_different(self):
        """
        Here we test a case when in one segment one or more columns has only null values. For example,
        Table A:
        | id   | c1 |  c2  |
        |------|----|------|
        | pk_1 | 1  | NULL |
        | pk_2 | 2  | NULL |
                ...
        | pk_n | n | NULL |

        Table B:
        | id   |   c1   |  c2  |
        |------|--------|------|
        | pk_1 | 1-diff | NULL |
        | pk_2 | 2-diff | NULL |
                  ...
        | pk_n | n-diff | NULL |

        To calculate a checksum, we need to concatenate string values by rows. If both tables have columns with NULL
        value, it may lead that concat(pk_i, i, NULL) == concat(pk_i, i-diff, NULL). This test handle such cases.
        """

        differ = HashDiffer(bisection_factor=2, bisection_threshold=4)
        diff = list(differ.diff_tables(self.a, self.b))
        self.assertEqual(diff, self.diffs)


@test_each_database
class TestTableTableEmpty(DiffTestCase):
    src_schema = {"id": str, "text_comment": str}
    dst_schema = {"id": str, "text_comment": str}

    def setUp(self):
        super().setUp()

        self.null_uuid = str(uuid.uuid1(1))

        diffs = [(str(uuid.uuid1(i)), str(i)) for i in range(100)]
        self.connection.query([self.src_table.insert_rows(diffs), commit])

        self.a = table_segment(self.connection, self.table_src_path, "id", "text_comment", case_sensitive=False)
        self.b = table_segment(self.connection, self.table_dst_path, "id", "text_comment", case_sensitive=False)

        self.differ = HashDiffer(bisection_factor=2)
        self.differ2 = HashDiffer(bisection_factor=2, allow_empty_tables=True)
        self.differ3 = JoinDiffer(allow_empty_tables=True)

    def test_empty_table(self):
        self.assertRaises(ValueError, list, self.differ.diff_tables(self.a, self.b))
        self.assertRaises(ValueError, list, self.differ.diff_tables(self.b, self.a))

    def test_empty_table2(self):
        diff1 = self.differ2.diff_tables(self.a, self.b)
        diff2 = self.differ2.diff_tables(self.b, self.a)
        a = list(diff1)
        b = list(diff2)
        assert len(a) == 100
        assert len(b) == 100
        assert all(i[0] == '-' for i in a)
        assert all(i[0] == '+' for i in b)
        assert {i[1] for i in a} == {i[1] for i in b}
        stats1 = diff1.get_stats_dict()
        assert stats1['rows_A'] == 100
        assert stats1['rows_B'] == 0
        stats2 = diff2.get_stats_dict()
        assert stats2['rows_A'] == 0
        assert stats2['rows_B'] == 100

    def test_empty_table2b(self):
        diff3 = self.differ2.diff_tables(self.b, self.b)
        assert not list(diff3)
        stats3 = diff3.get_stats_dict()
        assert stats3['rows_A'] == 0
        assert stats3['rows_B'] == 0

    def test_empty_table3(self):
        diff1 = self.differ3.diff_tables(self.a, self.b)
        diff2 = self.differ3.diff_tables(self.b, self.a)
        a = list(diff1)
        b = list(diff2)
        assert len(a) == 100
        assert len(b) == 100
        assert all(i[0] == '-' for i in a)
        assert all(i[0] == '+' for i in b)
        assert {i[1] for i in a} == {i[1] for i in b}
        stats1 = diff1.get_stats_dict()
        assert stats1['rows_A'] == 100
        assert stats1['rows_B'] == 0
        stats2 = diff2.get_stats_dict()
        assert stats2['rows_A'] == 0
        assert stats2['rows_B'] == 100

    def test_empty_table3b(self):
        diff3 = self.differ3.diff_tables(self.b, self.b)
        assert not list(diff3)
        stats3 = diff3.get_stats_dict()
        assert stats3['rows_A'] == 0
        assert stats3['rows_B'] == 0


class TestInfoTree(DiffTestCase):
    db_cls = db.MySQL
    src_schema = dst_schema = dict(id=int)

    def test_info_tree_root(self):
        db = self.connection
        db.query(
            [
                self.src_table.insert_rows([i] for i in range(1000)),
                self.dst_table.insert_rows([i] for i in range(2000)),
            ]
        )

        ts1 = TableSegment(db, self.src_table.path, ("id",))
        ts2 = TableSegment(db, self.dst_table.path, ("id",))

        for differ in (HashDiffer(bisection_threshold=64), JoinDiffer(True)):
            diff_res = differ.diff_tables(ts1, ts2)
            diff = list(diff_res)
            info_tree = diff_res.info_tree
            assert info_tree.info.is_diff
            assert info_tree.info.diff_count == 1000
            self.assertEqual(info_tree.info.rowcounts, {1: 1000, 2: 2000})


class TestDuplicateTables(DiffTestCase):
    db_cls = db.MySQL

    src_schema = {"id": int, "data": str}
    dst_schema = {"id": int, "data": str}

    def setUp(self):
        """
        table 1:
            (12, 'ABCDE'),
            (12, 'ABCDE');
        table 2:
            (4,'ABCDE'),
            (4,'ABCDE'),
            (4,'ABCDEF'),
            (6,'ABCDE'),
            (6,'ABCDE'),
            (6,'ABCDE');
        """

        super().setUp()

        src_values = [(12, "ABCDE"), (12, "ABCDE")]
        dst_values = [(4, "ABCDEF"), (4, "ABCDE"), (4, "ABCDE"), (6, "ABCDE"), (6, "ABCDE"), (6, "ABCDE")]

        self.diffs = sorted([("-", (str(r[0]), r[1])) for r in src_values]) + sorted([("+", (str(r[0]), r[1])) for r in dst_values])

        self.connection.query([self.src_table.insert_rows(src_values), self.dst_table.insert_rows(dst_values), commit])

        self.a = table_segment(
            self.connection, self.table_src_path, "id", extra_columns=("data",), case_sensitive=False
        )
        self.b = table_segment(
            self.connection, self.table_dst_path, "id", extra_columns=("data",), case_sensitive=False
        )

    def test_duplicates(self):
        """If there are duplicates in data, we want to return them as well"""

        differ = HashDiffer(bisection_factor=2, bisection_threshold=4)
        diff = list(differ.diff_tables(self.a, self.b))
        self.assertEqual(sorted(diff), sorted(self.diffs))


class TestDuplicates2(DiffTestCase):
    db_cls = db.MySQL

    src_schema = dst_schema = {"id": int, "data": str}

    def setUp(self):
        super().setUp()

        a = [
            (12, "ABCDE"),
            (12, "ABCDE"),
            (6, "ABCDE")
        ]

        b = [
            (4, "ABCDEF"),
            (4, "ABCDE"),
            (4, "ABCDE"),
            (6, "ABCDE"),
            (6, "ABCDE")
        ]

        self.expected_output_with_dups = [
            ('+', ("6", 'ABCDE')),
            ('+', ("4", 'ABCDE')),
            ('+', ("4", 'ABCDE')),
            ('+', ("4", 'ABCDEF')),
            ('-', ("12", 'ABCDE')),
            ('-', ("12", 'ABCDE')),
        ]

        self.expected_output_no_dups = [
            ('+', ("4", 'ABCDE')),
            ('+', ("4", 'ABCDEF')),
            ('-', ("12", 'ABCDE')),
        ]

        self.connection.query([self.src_table.insert_rows(a), self.dst_table.insert_rows(b), commit])

        self.a = table_segment(
            self.connection, self.table_src_path, "id", extra_columns=("data",), case_sensitive=False
        )
        self.b = table_segment(
            self.connection, self.table_dst_path, "id", extra_columns=("data",), case_sensitive=False
        )

    def test_duplicates2(self):
        """If there are duplicates in data, we want to return them as well"""

        differ = HashDiffer(bisection_factor=2, bisection_threshold=4, duplicate_rows_support=True)
        diff = list(differ.diff_tables(self.a, self.b))
        self.assertEqual(sorted(diff), sorted(self.expected_output_with_dups))

        differ = HashDiffer(bisection_factor=2, bisection_threshold=4, duplicate_rows_support=False)
        diff = list(differ.diff_tables(self.a, self.b))
        self.assertEqual(sorted(diff), sorted(self.expected_output_no_dups))


class TestSkipSortResults(DiffTestCase):
    db_cls = db.MySQL

    src_schema = dst_schema = {"id": int, "data": str}

    def setUp(self):
        super().setUp()

        a = [
            (1, "a"),
            (2, "b"),
            (3, "c"),
            (4, "x"),
        ]

        b = [
            (1, "a"),
            (2, "b"),
            (3, "z"),
            (4, "d"),
        ]

        self.expected_output_for_false = [
            ('-', ('3', 'c')),
            ('+', ('3', 'z')),
            ('+', ('4', 'd')),
            ('-', ('4', 'x')),
        ]

        self.expected_output_for_true = [
            ('+', ('3', 'z')),
            ('+', ('4', 'd')),
            ('-', ('3', 'c')),
            ('-', ('4', 'x')),
        ]

        self.connection.query([self.src_table.insert_rows(a), self.dst_table.insert_rows(b), commit])

        self.a = table_segment(
            self.connection, self.table_src_path, "id", extra_columns=("data",), case_sensitive=False
        )
        self.b = table_segment(
            self.connection, self.table_dst_path, "id", extra_columns=("data",), case_sensitive=False
        )

    def test_skip_sort_results(self):

        differ_false = HashDiffer(bisection_factor=2, bisection_threshold=4, skip_sort_results=False)
        diff_false = list(differ_false.diff_tables(self.a, self.b))
        self.assertEqual(diff_false, self.expected_output_for_false)

        differ_true = HashDiffer(bisection_factor=2, bisection_threshold=4, skip_sort_results=True)
        diff_true = list(differ_true.diff_tables(self.a, self.b))
        self.assertEqual(diff_true, self.expected_output_for_true)


@test_each_database
class TestCompoundKeySimple1(DiffTestCase):
    src_schema = {"id": int, "id2": int}
    dst_schema = {"id": int, "id2": int}

    def test_simple1(self):
        N = 1000
        K = N + 1
        V1 = N + 1
        V2 = N * 1000 + 2

        diffs = [(i, i + N) for i in range(N)]
        self.connection.query(
            [
                self.src_table.insert_rows(diffs + [(K, V1)]),
                self.dst_table.insert_rows(diffs + [(K, V2)]),
                commit,
            ]
        )

        expected = {("-", (str(K), str(V1))), ("+", (str(K), str(V2)))}
        differ = HashDiffer()

        a = TableSegment(self.connection, self.src_table.path, ("id",), extra_columns=("id2",))
        b = TableSegment(self.connection, self.dst_table.path, ("id",), extra_columns=("id2",))
        diff = set(differ.diff_tables(a, b))
        self.assertEqual(diff, expected)

        aa = TableSegment(self.connection, self.src_table.path, ("id", "id2"))
        bb = TableSegment(self.connection, self.dst_table.path, ("id", "id2"))
        diff = set(differ.diff_tables(aa, bb))
        self.assertEqual(diff, expected)


@test_each_database
class TestCompoundKeySimple2(DiffTestCase):
    src_schema = {"id": int, "id2": int}
    dst_schema = {"id": int, "id2": int}

    def test_simple2(self):
        N = 1000
        K = N + 1
        V1 = N + 1
        V2 = N * 1000 + 2

        diffs = [(i, i + N) for i in range(N)]
        self.connection.query(
            [
                self.src_table.insert_rows(diffs + [(K, V1)]),
                self.dst_table.insert_rows(diffs + [(0, V2)]),
                commit,
            ]
        )

        expected = {("-", (str(K), str(V1))), ("+", (str(0), str(V2)))}
        differ = HashDiffer()

        a = TableSegment(self.connection, self.src_table.path, ("id",), extra_columns=("id2",))
        b = TableSegment(self.connection, self.dst_table.path, ("id",), extra_columns=("id2",))
        diff = set(differ.diff_tables(a, b))
        self.assertEqual(diff, expected)

        aa = TableSegment(self.connection, self.src_table.path, ("id", "id2"))
        bb = TableSegment(self.connection, self.dst_table.path, ("id", "id2"))
        diff = set(differ.diff_tables(aa, bb))
        self.assertEqual(diff, expected)


@test_each_database
class TestCompoundKeySimple3(DiffTestCase):
    src_schema = {"id": int, "id2": int}
    dst_schema = {"id": int, "id2": int}

    def test_negative_keys(self):
        N = 1000
        K = -N + 1
        V1 = N + 1
        V2 = -N * 1000 + 2

        diffs = [(i, i + N) for i in range(N)]
        self.connection.query(
            [
                self.src_table.insert_rows(diffs + [(K, V1)]),
                self.dst_table.insert_rows(diffs + [(K, V2)]),
                commit,
            ]
        )

        expected = {("-", (str(K), str(V1))), ("+", (str(K), str(V2)))}
        differ = HashDiffer()

        a = TableSegment(self.connection, self.src_table.path, ("id",), extra_columns=("id2",))
        b = TableSegment(self.connection, self.dst_table.path, ("id",), extra_columns=("id2",))
        diff = set(differ.diff_tables(a, b))
        self.assertEqual(diff, expected)

        aa = TableSegment(self.connection, self.src_table.path, ("id", "id2"))
        bb = TableSegment(self.connection, self.dst_table.path, ("id", "id2"))
        diff = set(differ.diff_tables(aa, bb))
        self.assertEqual(diff, expected)


# Alphanumeric keys whose code point order differs from MySQL's case-insensitive collations:
# in binary order 'B' < 'Z9' < '_x' < 'a1', in utf8mb4_0900_ai_ci '_x' < 'a1' < 'B' < 'Z9'.
# No two keys are equal under a case-insensitive collation, so they fit a PRIMARY KEY of any collation.
# fmt: off
MIXED_CASE_KEYS = [
    "B", "a1", "C", "b2", "Z9", "z10", "_x", "A_", "0a", "9Z", "Ab", "aC", "M", "M-", "M.", "m0",
    "Q1", "q2", "R", "r3", "S2x", "s2Y", "T_", "t_1", "U", "u4", "Vv", "vW", "W9", "w8", "X", "x5", "Y.", "y6",
]
# fmt: on
MIXED_CASE_ROWS = [(k, f"value of {k}") for k in MIXED_CASE_KEYS]
# The second table lacks 'Vv' and has another value for 'aC'
MIXED_CASE_ROWS_CHANGED = [(k, "CHANGED" if k == "aC" else v) for k, v in MIXED_CASE_ROWS if k != "Vv"]
MIXED_CASE_DIFF = [("+", ("aC", "CHANGED")), ("-", ("Vv", "value of Vv")), ("-", ("aC", "value of aC"))]

MYSQL_STRING_KEY_TYPES = [
    "VARCHAR(16) COLLATE utf8mb4_0900_ai_ci",  # MySQL 8 default
    "VARCHAR(16) COLLATE utf8mb4_general_ci",  # MySQL 5.7 default
    "VARCHAR(16) COLLATE utf8mb4_0900_as_cs",
    "VARCHAR(16) COLLATE utf8mb4_bin",
    "VARCHAR(16) COLLATE utf8mb4_0900_bin",
    "VARCHAR(16) CHARACTER SET latin1 COLLATE latin1_swedish_ci",
    "VARCHAR(16) CHARACTER SET utf16 COLLATE utf16_general_ci",  # code units are not bytes
    "VARBINARY(16)",
]


@test_each_database_in_list({db.MySQL})
class TestMySQLStringKeyCollations(DiffTestCase):
    """String keys on MySQL, whose collations don't order strings the way Python does (erezsh/sqeleton#22).

    Checks that each column type is detected as an alphanumeric key, and that a same-database diff is right.
    Both tables share a collation here, so this can't tell whether the key ranges are ordered like Python:
    TestMySQLStringKeysAcrossDatabases checks that.
    """

    def _create_tables(self, key_type):
        for name, rows in [(self.table_src_name, MIXED_CASE_ROWS), (self.table_dst_name, MIXED_CASE_ROWS_CHANGED)]:
            self.connection.query(f"CREATE TABLE {name} (id {key_type} PRIMARY KEY, v VARCHAR(64))")
            self.connection.query([table(self.connection.parse_table_name(name)).insert_rows(rows), commit])

    @parameterized.expand([(t,) for t in MYSQL_STRING_KEY_TYPES])
    def test_string_key(self, key_type):
        self._create_tables(key_type)
        a = TableSegment(self.connection, self.table_src_path, ("id",), extra_columns=("v",))
        b = TableSegment(self.connection, self.table_dst_path, ("id",), extra_columns=("v",))

        collation = key_type.split("COLLATE ")[1] if "COLLATE" in key_type else "binary"
        self.assertEqual(a.with_schema()._schema["id"], String_VaryingAlphanum(collation=collation))

        differ = HashDiffer(bisection_factor=3, bisection_threshold=4)
        self.assertEqual(sorted(differ.diff_tables(a, b)), MIXED_CASE_DIFF)
        self.assertEqual(list(differ.diff_tables(a, a)), [])


@unittest.skipUnless(db.MySQL in CONN_STRINGS, "Requires MySQL")
class TestMySQLStringKeysAcrossDatabases(unittest.TestCase):
    """MySQL (collation order) against DuckDB (code point order, like Snowflake or BigQuery).

    Both sides must put each key in the same segment, or rows get reported missing on one side and extra on the
    other. Bisection is forced with a tiny bisection_threshold, so the key range predicates are what gets tested.
    Runs with the expression the server supports ('detected': collate utf8mb4_0900_bin on MySQL 8.0.17+), and
    with the one for servers without a NO PAD binary collation ('fallback': cast as binary, MySQL 5.7).
    """

    def setUp(self):
        # Own connection, since the fallback mode changes its dialect (get_conn(shared=False) returns the cached one)
        self.mysql = connect(CONN_STRINGS[db.MySQL], N_THREADS, shared=False)
        self.duckdb = get_conn(db.DuckDB)
        suffix = random_table_suffix()
        self.mysql_name = f"src{suffix}"
        self.mysql_path = self.mysql.parse_table_name(self.mysql_name)
        self.duckdb_path = self.duckdb.parse_table_name(f"dst{suffix}")
        drop_table(self.mysql, self.mysql_path)
        drop_table(self.duckdb, self.duckdb_path)

    def tearDown(self):
        drop_table(self.mysql, self.mysql_path)
        drop_table(self.duckdb, self.duckdb_path)
        self.mysql.close()

    def _create_tables(self, key_type, mysql_rows, duckdb_rows):
        self.mysql.query(f"CREATE TABLE {self.mysql_name} (id {key_type} PRIMARY KEY, v VARCHAR(64))")
        self.mysql.query([table(self.mysql_path).insert_rows(mysql_rows), commit])
        self.duckdb.query(table(self.duckdb_path, schema={"id": str, "v": str}).create())
        self.duckdb.query([table(self.duckdb_path).insert_rows(duckdb_rows), commit])

    def _segments(self, mode):
        a = TableSegment(self.mysql, self.mysql_path, ("id",), extra_columns=("v",)).with_schema()
        b = TableSegment(self.duckdb, self.duckdb_path, ("id",), extra_columns=("v",)).with_schema()
        self.assertIn(self.mysql.dialect.nopad_binary_collation, NOPAD_BINARY_COLLATIONS)
        if mode == "fallback":
            self.mysql.dialect.nopad_binary_collation = None
        return a, b

    @parameterized.expand([(t, mode) for mode in ("detected", "fallback") for t in MYSQL_STRING_KEY_TYPES])
    def test_diff(self, key_type, mode):
        self._create_tables(key_type, MIXED_CASE_ROWS, MIXED_CASE_ROWS_CHANGED)
        a, b = self._segments(mode)
        differ = HashDiffer(bisection_factor=3, bisection_threshold=4)
        self.assertEqual(sorted(differ.diff_tables(a, b)), MIXED_CASE_DIFF)
        flipped = sorted(("-" if sign == "+" else "+", row) for sign, row in MIXED_CASE_DIFF)
        self.assertEqual(sorted(differ.diff_tables(b, a)), flipped)

    @parameterized.expand([("detected",), ("fallback",)])
    def test_non_alphanumeric_key_in_latin1(self, mode):
        """Only 64 rows are sampled to detect alphanumeric keys, so a later 'é' still reaches the key range.
        Its latin1 byte isn't valid UTF-8: compared as raw bytes, min/max came back NULL, which read as
        'table is empty' (or as no differences, with allow_empty_tables). It must fail like on other databases.
        The DuckDB side lacks the 'é' row, so that its own key range can't raise the expected error instead."""
        rows = [(f"k{i:03}", str(i)) for i in range(70)]
        self._create_tables(
            "VARCHAR(16) CHARACTER SET latin1 COLLATE latin1_swedish_ci", rows + [("zz\u00e9", "70")], rows
        )
        a, b = self._segments(mode)
        self.assertEqual(a._schema["id"], String_VaryingAlphanum(collation="latin1_swedish_ci"))
        self.assertEqual(a.query_key_range(), (("k000",), ("zz\u00e9",)))
        for allow_empty_tables in (False, True):
            differ = HashDiffer(bisection_factor=3, bisection_threshold=4, allow_empty_tables=allow_empty_tables)
            self.assertRaisesRegex(ValueError, "Cannot apply", list, differ.diff_tables(a, b))


@test_each_database
class TestCompoundKeyAlphanum(DiffTestCase):
    src_schema = {"id": str, "id2": int, "comment": str}
    dst_schema = {"id": str, "id2": int, "comment": str}

    def setUp(self):
        super().setUp()

        rows = [(str(uuid.uuid1(i)), i, str(i)) for i in range(100)]
        rows2 = list(rows)
        x = rows2[9]
        rows2[9] = (x[0], 9000, x[2])
        self.connection.query(
            [
                self.src_table.insert_rows(rows),
                self.dst_table.insert_rows(rows2),
                commit,
            ]
        )

    def test_compound_key(self):
        a = TableSegment(self.connection, self.src_table.path, ("id",), extra_columns=("id2", "comment"))
        b = TableSegment(self.connection, self.dst_table.path, ("id",), extra_columns=("id2", "comment"))

        differ = HashDiffer()
        diff = list(differ.diff_tables(a, b))
        uuid = diff[0][1][0]
        self.assertEqual(diff, [("-", (uuid, "9", "9")), ("+", (uuid, "9000", "9"))])

        aa = TableSegment(self.connection, self.src_table.path, ("id", "id2"), "comment")
        bb = TableSegment(self.connection, self.dst_table.path, ("id", "id2"), "comment")
        diff = list(differ.diff_tables(aa, bb))
        uuid = diff[0][1][0]
        self.assertEqual(diff, [("-", (uuid, "9", "9")), ("+", (uuid, "9000", "9"))])

        self.assertRaises(ValueError, list, differ.diff_tables(aa, a))


class TestDiffSets(unittest.TestCase):
    def test_compare_element(self):
        self.assertEqual(compare_element(None, 1), -1)
        self.assertEqual(compare_element(1, 2), -1)
        self.assertEqual(compare_element(None, 2), -1)
        self.assertEqual(compare_element(None, None), 0)
        self.assertEqual(compare_element(1, 1), 0)
        self.assertEqual(compare_element(2, 2), 0)
        self.assertEqual(compare_element(1, None), 1)
        self.assertEqual(compare_element(2, 1), 1)
        self.assertEqual(compare_element(2, None), 1)
        self.assertEqual(compare_element(None, ''), -1)
        self.assertEqual(compare_element('', ''), 0)
        self.assertEqual(compare_element('', None), 1)

    def test_diff_sets(self):
        res = diff_sets([(1, None)], [(1, '')], skip_sort_results=False, duplicate_rows_support=True)
        self.assertSequenceEqual(res, [('-', (1, None)), ('+', (1, ''))])

        res = diff_sets([(1, '')], [(1, None)], skip_sort_results=False, duplicate_rows_support=True)
        self.assertSequenceEqual(res, [('+', (1, None)), ('-', (1, ''))])

        res = diff_sets([(1, None), (1, None)], [(1, None)], skip_sort_results=False, duplicate_rows_support=True)
        self.assertSequenceEqual(res, [('-', (1, None))])
