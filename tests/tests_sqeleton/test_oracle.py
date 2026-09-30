import unittest
from unittest.mock import MagicMock, patch

from namidiff.sqeleton.databases.base import ConnectError
from namidiff.sqeleton.databases.oracle import Oracle


def _fake_oracledb(thin=True):
    m = MagicMock()
    m.is_thin_mode.return_value = thin
    return m


class TestOracleClientMode(unittest.TestCase):
    def _connect(self, fake, **kw):
        with patch("namidiff.sqeleton.databases.oracle.import_oracle", return_value=fake):
            return Oracle(host="h", database="svc", thread_count=1, user="u", password="p", **kw)

    def test_thin_by_default(self):
        fake = _fake_oracledb()
        db = self._connect(fake)
        fake.init_oracle_client.assert_not_called()
        # Mode options are not passed on to oracledb.connect()
        assert db.kwargs == {"user": "u", "password": "p", "dsn": "h:1521/svc"}

    def test_thick_mode(self):
        for kw, lib_dir in [
            ({"thick_mode": "true"}, None),  # URI params arrive as strings
            ({"thick_mode": True}, None),  # TOML / dict config
            ({"lib_dir": "/opt/ic"}, "/opt/ic"),  # lib_dir implies thick mode
        ]:
            fake = _fake_oracledb()
            self._connect(fake, **kw)
            fake.init_oracle_client.assert_called_once_with(lib_dir=lib_dir)

    def test_thick_mode_off(self):
        fake = _fake_oracledb()
        self._connect(fake, thick_mode="false")
        fake.init_oracle_client.assert_not_called()

    def test_thick_mode_already_enabled(self):
        fake = _fake_oracledb(thin=False)
        self._connect(fake, thick_mode="true")
        fake.init_oracle_client.assert_not_called()

    def test_thick_mode_missing_client(self):
        fake = _fake_oracledb()
        fake.init_oracle_client.side_effect = Exception("DPI-1047: Cannot locate a 64-bit Oracle Client library")
        with self.assertRaisesRegex(ConnectError, "DPI-1047.*Instant Client"):
            self._connect(fake, lib_dir="/nope")
