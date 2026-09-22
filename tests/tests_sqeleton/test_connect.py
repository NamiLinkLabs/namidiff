"""Tests for URI/dict parsing in sqeleton's Connect, using fake database classes (no real connections)."""

import os
import tempfile
import unittest

from namidiff.sqeleton.abcs.mixins import AbstractMixin_NormalizeValue
from namidiff.sqeleton.databases import Connect
from namidiff.sqeleton.databases.base import Database, ThreadedDatabase
from namidiff.sqeleton.databases._connect import DATABASE_BY_SCHEME


class FakeDB(Database):
    CONNECT_URI_HELP = "fake://<user>@<host>/<database>/<schema?>"
    CONNECT_URI_PARAMS = ["database", "schema?"]
    CONNECT_URI_KWPARAMS = []
    is_closed = False

    def __init__(self, **kw):
        self.kw = kw


class FakeKwDB(FakeDB):
    CONNECT_URI_PARAMS = ["database"]
    CONNECT_URI_KWPARAMS = ["warehouse"]


class FakeThreadedDB(ThreadedDatabase):
    CONNECT_URI_HELP = "threaded://<host>/<database?>"
    CONNECT_URI_PARAMS = ["database?"]

    def __init__(self, thread_count=1, **kw):
        self.thread_count = thread_count
        self.kw = kw


# Allow instantiating the fakes, without implementing the abstract database API
for _cls in (FakeDB, FakeKwDB, FakeThreadedDB):
    _cls.__abstractmethods__ = frozenset()


def make_connect(**extra):
    return Connect(
        {
            "fake": FakeDB,
            "snowflake": FakeKwDB,
            "bigquery": FakeDB,
            "databricks": FakeDB,
            "threaded": FakeThreadedDB,
            **extra,
        }
    )


class TestConnect(unittest.TestCase):
    def setUp(self):
        self.connect = make_connect()

    def test_uri_params(self):
        db = self.connect("fake://user:pw@host:1234/mydb/myschema", shared=False)
        assert db.kw == {
            "database": "mydb",
            "schema": "myschema",
            "host": "host",
            "port": 1234,
            "user": "user",
            "password": "pw",
        }

        # Optional param omitted, and no password
        db = self.connect("fake://user@host/mydb", shared=False)
        assert db.kw == {"database": "mydb", "host": "host", "user": "user"}

        # Params can be given as query arguments
        db = self.connect("fake://user@host?database=mydb&extra=1", shared=False)
        assert db.kw["database"] == "mydb" and db.kw["extra"] == "1"

    def test_uri_param_errors(self):
        with self.assertRaisesRegex(ValueError, "Too many parts to path"):
            self.connect("fake://user@host/a/b/c", shared=False)
        with self.assertRaisesRegex(ValueError, "URI must specify 'database'"):
            self.connect("fake://user@host", shared=False)
        with self.assertRaisesRegex(ValueError, "already provided as positional argument"):
            self.connect("fake://user@host/mydb?database=other", shared=False)
        with self.assertRaisesRegex(ValueError, "URI must specify 'warehouse'"):
            self.connect("snowflake://user:pw@account/mydb", shared=False)
        with self.assertRaisesRegex(NotImplementedError, "Scheme 'nope' currently not supported"):
            self.connect("nope://host/db", shared=False)
        with self.assertRaisesRegex(NotImplementedError, "multiple schemes"):
            self.connect("fake+other://host/db", shared=False)
        with self.assertRaisesRegex(TypeError, "must be a URI string or a dictionary"):
            self.connect(42, shared=False)

    def test_special_schemes(self):
        db = self.connect("snowflake://user:pw@account/mydb?warehouse=wh", shared=False)
        assert db.kw == {"database": "mydb", "warehouse": "wh", "account": "account", "user": "user", "password": "pw"}

        db = self.connect("bigquery://project/dataset", shared=False)
        assert db.kw == {"database": "dataset", "schema": None, "project": "project"}  # Returned before None-filtering

        db = self.connect("databricks://:token@hostname/http/path?catalog=c", shared=False)
        assert db.kw == {
            "access_token": "token",
            "http_path": "/http/path",
            "server_hostname": "hostname",
            "catalog": "c",
        }

        db = self.connect("threaded://host/db", thread_count=4, shared=False)
        assert db.thread_count == 4 and db.kw == {"database": "db", "host": "host"}

    def test_toml(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write('[database.mydb]\ndriver = "fake"\ndatabase = "x"\n')
        try:
            db = self.connect(f"toml://{f.name}#mydb", shared=False)
            assert db.kw == {"database": "x"}
            with self.assertRaisesRegex(ValueError, "Cannot find database config named 'other'"):
                self.connect(f"toml://{f.name}#other", shared=False)
            with self.assertRaisesRegex(ValueError, "Must specify a database name"):
                self.connect(f"toml://{f.name}", shared=False)
        finally:
            os.unlink(f.name)

    def test_connect_with_dict(self):
        db = self.connect({"driver": "fake", "database": "x"}, shared=False)
        assert isinstance(db, FakeDB) and db.kw == {"database": "x"}

        db = self.connect({"driver": "threaded", "database": "x"}, thread_count=3, shared=False)
        assert db.thread_count == 3

        with self.assertRaisesRegex(NotImplementedError, "Driver 'nope' currently not supported"):
            self.connect({"driver": "nope"}, shared=False)

    def test_shared_cache(self):
        a = self.connect({"driver": "fake", "database": "x"})
        b = self.connect({"driver": "fake", "database": "x"})
        assert a is b

        a.is_closed = True
        c = self.connect({"driver": "fake", "database": "x"})
        assert c is not a

    def test_for_databases_and_load_mixins(self):
        c = Connect(DATABASE_BY_SCHEME).for_databases("duckdb", "mysql")
        assert set(c.database_by_scheme) == {"duckdb", "mysql"}

        c = c.load_mixins(AbstractMixin_NormalizeValue)
        for name, cls in c.database_by_scheme.items():
            assert cls.__name__ == DATABASE_BY_SCHEME[name].__name__
            assert hasattr(cls.dialect, "normalize_value_by_type")
