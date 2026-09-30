import re
import threading
import unittest
from datetime import datetime, timedelta

from namidiff.parse_time import ParseError, parse_time_before, parse_time_delta
from namidiff.utils import (
    Vector,
    accumulate,
    eval_name_template,
    getLogger,
    match_like,
    run_as_daemon,
    safezip,
    truncate_error,
)


class TestUtils(unittest.TestCase):
    def test_safezip(self):
        assert list(safezip([1, 2], "ab")) == [(1, "a"), (2, "b")]
        self.assertRaises(ValueError, safezip, [1, 2], [1])

    def test_match_like(self):
        strs = ["foo", "foobar", "bar", "fox"]
        assert list(match_like("foo%", strs)) == ["foo", "foobar"]
        assert list(match_like("fo?", strs)) == ["foo", "fox"]
        assert list(match_like("baz", strs)) == []

    def test_accumulate(self):
        assert list(accumulate([1, 2, 3])) == [1, 3, 6]
        assert list(accumulate([1, 2, 3], initial=10)) == [10, 11, 13, 16]
        assert list(accumulate([])) == []
        assert list(accumulate([], initial=5)) == [5]

    def test_run_as_daemon(self):
        done = threading.Event()
        th = run_as_daemon(done.set)
        th.join(5)
        assert th.daemon
        assert done.is_set()

    def test_get_logger(self):
        assert getLogger("namidiff.hashdiff_tables").name == "hashdiff_tables"

    def test_eval_name_template(self):
        assert eval_name_template("plain") == "plain"
        res = eval_name_template("tbl_%t")
        assert re.fullmatch(r"tbl_\d{4}-\d\d-\d\d_\d\d_\d\d_\d\d", res), res

    def test_truncate_error(self):
        assert truncate_error("bad 'secret' value\nsecond line 'x'") == "bad '***' value"

    def test_vector(self):
        a = Vector((1, 2))
        b = Vector((2, 3))
        assert a < b and a <= b and b > a and b >= a
        assert not (a < Vector((2, 2)))
        assert a == Vector((1, 2))
        assert b - a == Vector((1, 1))
        assert repr(a) == "(1, 2)"

        for op in ("__lt__", "__le__", "__gt__", "__ge__", "__eq__"):
            assert getattr(a, op)((1, 2)) is NotImplemented
        self.assertRaises(NotImplementedError, lambda: a - (1, 1))


class TestParseTime(unittest.TestCase):
    def test_errors(self):
        self.assertRaises(ParseError, parse_time_delta, "abc")
        self.assertRaises(ParseError, parse_time_delta, "1d2d")
        with self.assertRaisesRegex(ParseError, "Did you mean 'min'"):
            parse_time_delta("5mi")

    def test_parse_time_before(self):
        now = datetime(2020, 1, 10)
        assert parse_time_before(now, "2d") == now - timedelta(days=2)
