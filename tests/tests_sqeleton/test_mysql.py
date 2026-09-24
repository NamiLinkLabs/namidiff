import unittest

from namidiff.sqeleton import connect
from namidiff.sqeleton import databases as db
from namidiff.sqeleton.abcs.database_types import Timestamp
from namidiff.sqeleton.abcs.mixins import AbstractMixin_NormalizeValue
from namidiff.sqeleton.databases.base import ConnectError
from namidiff.sqeleton.databases.mysql import Dialect, MySQL

from .common import CONN_STRINGS, get_conn


class TestMySQLDialect(unittest.TestCase):
    def test_sql_generation(self):
        d = Dialect.load_mixins(AbstractMixin_NormalizeValue)
        assert d.optimizer_hints("X") == "/*+ X */ "
        sql = d.normalize_timestamp("ts", Timestamp(precision=3, rounds=True))
        assert "datetime(3)" in sql and "datetime(6)" in sql

    def test_requires_database(self):
        with self.assertRaisesRegex(ValueError, "must specify a database"):
            MySQL(thread_count=1, host="localhost")


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
