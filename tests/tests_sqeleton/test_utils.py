import unittest

from namidiff.sqeleton.utils import remove_passwords_in_dict, match_regexps, match_like, number_to_human, WeakCache


class TestUtils(unittest.TestCase):
    def test_remove_passwords_in_dict(self):
        # Test replacing password value
        d = {"password": "mypassword"}
        remove_passwords_in_dict(d)
        assert d["password"] == "***"

        # Test replacing password in database URL
        d = {"database_url": "mysql://user:mypassword@localhost/db"}
        remove_passwords_in_dict(d, "$$$$")
        assert d["database_url"] == "mysql://user:$$$$@localhost/db"

        # Test replacing password in nested dictionary
        d = {"info": {"password": "mypassword"}}
        remove_passwords_in_dict(d, "%%")
        assert d["info"]["password"] == "%%"

    def test_match_regexps(self):
        def only_results(x):
            return [v for k, v in x]

        # Test with no matches
        regexps = {"a*": 1, "b*": 2}
        s = "c"
        assert only_results(match_regexps(regexps, s)) == []

        # Test with one match
        regexps = {"a*": 1, "b*": 2}
        s = "b"
        assert only_results(match_regexps(regexps, s)) == [2]

        # Test with multiple matches
        regexps = {"abc": 1, "ab*c": 2, "c*": 3}
        s = "abc"
        assert only_results(match_regexps(regexps, s)) == [1, 2]

        # Test with regexp that doesn't match the end of the string
        regexps = {"a*b": 1}
        s = "acb"
        assert only_results(match_regexps(regexps, s)) == []

    def test_match_like(self):
        strs = ["abc", "abcd", "ab", "bcd", "def"]

        # Test exact match
        pattern = "abc"
        result = list(match_like(pattern, strs))
        assert result == ["abc"]

        # Test % match
        pattern = "a%"
        result = list(match_like(pattern, strs))
        self.assertEqual(result, ["abc", "abcd", "ab"])

        # Test ? match
        pattern = "a?c"
        result = list(match_like(pattern, strs))
        self.assertEqual(result, ["abc"])

    def test_number_to_human(self):
        # Test basic conversion
        assert number_to_human(1000) == "1k"
        assert number_to_human(1000000) == "1m"
        assert number_to_human(1000000000) == "1b"

        # Test decimal values
        assert number_to_human(1234) == "1k"
        assert number_to_human(12345) == "12k"
        assert number_to_human(123456) == "123k"
        assert number_to_human(1234567) == "1m"
        assert number_to_human(12345678) == "12m"
        assert number_to_human(123456789) == "123m"
        assert number_to_human(1234567890) == "1b"

        # Test negative values
        assert number_to_human(-1000) == "-1k"
        assert number_to_human(-1000000) == "-1m"
        assert number_to_human(-1000000000) == "-1b"

    def test_weak_cache(self):
        # Create cache
        cache = WeakCache()

        # Test adding and retrieving basic value
        o = {1, 2}
        cache.add("key", o)
        assert cache.get("key") is o

        # Test adding and retrieving dict value
        cache.add({"key": "value"}, o)
        assert cache.get({"key": "value"}) is o

        # Test deleting value when reference is lost
        del o
        try:
            cache.get({"key": "value"})
            assert False, "KeyError should have been raised"
        except KeyError:
            pass


class TestUtilsExtra(unittest.TestCase):
    def test_join_iter_and_safezip(self):
        from namidiff.sqeleton.utils import join_iter, safezip

        assert list(join_iter(",", [])) == []
        assert list(join_iter(",", "ab")) == ["a", ",", "b"]
        self.assertRaises(ValueError, safezip, [1], [1, 2])

    def test_case_dicts(self):
        from namidiff.sqeleton.utils import CaseInsensitiveDict, CaseSensitiveDict

        d = CaseInsensitiveDict({"Foo": 1})
        d["FOO"] = 2
        assert d.get_key("foo") == "Foo" and d["foo"] == 2
        assert isinstance(d.new(), CaseInsensitiveDict) and len(d.new()) == 0
        del d["FOO"]
        assert len(d) == 0

        s = CaseSensitiveDict({"Foo": 1})
        ci = s.as_insensitive()
        assert isinstance(ci, CaseInsensitiveDict) and ci["foo"] == 1

    def test_arith_uuid(self):
        from uuid import UUID
        from namidiff.sqeleton.utils import ArithUUID

        u = ArithUUID(int=10)
        assert int(u) == 10
        assert (u + 1).int == 11 and (u - 1).int == 9
        assert u - ArithUUID(int=4) == 6
        assert u.__add__("x") is NotImplemented
        assert u.__sub__("x") is NotImplemented
        assert isinstance(u - UUID(int=1), int)

    def test_arith_alphanumeric(self):
        from namidiff.sqeleton.utils import ArithAlphanumeric

        a = ArithAlphanumeric("abc")
        assert repr(a) == 'alphanum"abc"'
        with self.assertRaises(ValueError):
            ArithAlphanumeric("abcdef", max_len=3)
        assert a.__add__("x") is NotImplemented
        assert a.__sub__("x") is NotImplemented
        assert a.__ge__(1) is NotImplemented
        assert a.__lt__(1) is NotImplemented
        assert a.__eq__(1) is NotImplemented
        assert a == ArithAlphanumeric("abc") and a != ArithAlphanumeric("abd")

    def test_arith_alphanumeric_successor(self):
        from namidiff.sqeleton.utils import ArithAlphanumeric

        # '+ 1' is the exclusive upper bound after the max key, so it must sort after the original
        for s in ["z", "zz", "zzz", "a", "0a", "M ", "-", "Az_"]:
            a = ArithAlphanumeric(s)
            assert a < a + 1, (s, str(a + 1))
            assert (a + 1) - a > 0
        assert str(ArithAlphanumeric("zz") + 1) == "zz-"

    def test_arith_alphanumeric_sub_matches_string_order(self):
        import random
        from namidiff.sqeleton.utils import ArithAlphanumeric, alphanums

        A = ArithAlphanumeric
        assert A("M ") - A("M") > 0
        assert A("M") - A("M ") < 0
        assert A("M") - A("M") == 0
        assert A("b") - A("a") > 0 and A("a") - A("") > 0

        rng = random.Random(0)
        words = ["".join(rng.choice(alphanums) for _ in range(rng.randint(0, 4))) for _ in range(300)]
        words += ["M", "M ", "M  ", "z", "zz", " ", "  "]
        for w1, w2 in zip(words, words[1:] + words[:1]):
            d = A(w2) - A(w1)
            assert (d > 0) == (w2 > w1) and (d == 0) == (w2 == w1), (w1, w2, d)

    def test_arith_alphanumeric_range(self):
        import random
        from namidiff.sqeleton.utils import ArithAlphanumeric, alphanums

        A = ArithAlphanumeric
        rng = random.Random(1)
        pairs = [("0a", "zz-"), ("M", "M "), ("M", "Mc-"), ("a", "b"), ("", "zzzz"), ("a b", "a z")]
        for _ in range(200):
            w = sorted("".join(rng.choice(alphanums) for _ in range(rng.randint(0, 5))) for _ in range(2))
            if w[0] != w[1]:
                pairs.append(tuple(w))
        for lo, hi in pairs:
            for count in (1, 2, 7, 32):
                count = min(count, A(hi) - A(lo))  # split_key_space never asks for more points than fit
                points = [str(p) for p in A(lo).range(A(hi), count)]
                assert len(points) <= count
                # Strictly increasing and strictly inside the bounds, as split_key_space asserts
                assert all(x < y for x, y in zip([lo] + points, points + [hi])), (lo, hi, count, points)
        assert A("M").range(A("M "), 1) == []
        assert len(A("0a").range(A("zz-"), 4)) == 4

    def test_alphanumeric_key_segments(self):
        from namidiff.sqeleton.utils import ArithAlphanumeric as A
        from namidiff.table_segment import Vector, split_compound_key_space

        # Regions from the second pass that differ only by a trailing space have a positive size
        mn, mx = Vector([A("M")]), Vector([A("M ")])
        assert all(d > 0 for d in mx - mn)
        assert split_compound_key_space(mn, mx, 4) == [[A("M"), A("M ")]]
        points = split_compound_key_space(Vector([A("0a")]), Vector([A("zz") + 1]), 8)[0]
        assert points[0] == A("0a") and points[-1] == A("zz-") and len(points) == 10

    def test_remove_password_no_password(self):
        from namidiff.sqeleton.utils import _join_if_any, remove_password_from_url

        assert _join_if_any(",", []) == ""
        assert remove_password_from_url("mysql://localhost/db") == "mysql://localhost/db"

    def test_unknown(self):
        from namidiff.sqeleton.utils import Unknown

        assert isinstance(Unknown, Unknown)
        assert repr(Unknown) == "Unknown"
        self.assertRaises(RuntimeError, Unknown)
        self.assertRaises(TypeError, Unknown.__nonzero__, None)
