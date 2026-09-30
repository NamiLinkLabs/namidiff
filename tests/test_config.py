import os
import unittest

from namidiff.config import apply_config_from_string, apply_config_from_file, ConfigParseError
from namidiff.utils import remove_password_from_url


class TestConfig(unittest.TestCase):
    def test_basic(self):
        config = r"""
            [database.test_postgresql]
            driver = "postgresql"
            user = "postgres"
            password = "Password1"

            [run.default]
            update_column = "timestamp"
            verbose = true
            threads = 2

            [run.pg_pg]
            threads = 4
            1.database = "test_postgresql"
            1.table = "rating"
            1.threads = 11
            2.database = "postgresql://postgres:Password1@/"
            2.table = "rating_del1"
            2.threads = 22
        """
        self.assertRaises(ConfigParseError, apply_config_from_string, config, "bla", {})  # No such run

        res = apply_config_from_string(config, "pg_pg", {})
        assert res["update_column"] == "timestamp"  # default
        assert res["verbose"] is True
        assert res["threads"] == 4  # overwritten by pg_pg
        assert res["database1"] == {"driver": "postgresql", "user": "postgres", "password": "Password1"}
        assert res["database2"] == "postgresql://postgres:Password1@/"
        assert res["table1"] == "rating"
        assert res["table2"] == "rating_del1"
        assert res["threads1"] == 11
        assert res["threads2"] == 22

        res = apply_config_from_string(config, "pg_pg", {"update_column": "foo", "table2": "bar"})
        assert res["update_column"] == "foo"
        assert res["table2"] == "bar"

    def test_remove_password(self):
        replace_with = "*****"
        urls = [
            "d://host/",
            "d://host:123/",
            "d://user@host:123/",
            "d://user:PASS@host:123/",
            "d://:PASS@host:123/",
            "d://:PASS@host:123/path",
            "d://:PASS@host:123/path?whatever#blabla",
        ]
        for url in urls:
            removed = remove_password_from_url(url, replace_with)
            expected = url.replace("PASS", replace_with)
            removed = remove_password_from_url(url, replace_with)
            self.assertEqual(removed, expected)

    def test_embed_env(self):
        env = {
            "DRIVER": "postgresql",
            "USER": "postgres",
            "PASSWORD": "Password1",
            "RUN_PG_1_DATABASE": "test_postgresql",
            "RUN_PG_1_TABLE": "rating",
            "RUN_PG_2_DATABASE": "postgresql://postgres:Password1@/",
            "RUN_PG_2_TABLE": "rating_del1",
        }
        config = r"""
            [database.test_postgresql]
            driver = "${DRIVER}"
            user = "${USER}"
            password = "${PASSWORD}"

            [run.default]
            update_column = "${UPDATE_COLUMN}"
            verbose = true
            threads = 2

            [run.pg_pg]
            threads = 4
            1.database = "${RUN_PG_1_DATABASE}"
            1.table = "${RUN_PG_1_TABLE}"
            1.threads = 11
            2.database = "${RUN_PG_2_DATABASE}"
            2.table = "${RUN_PG_2_TABLE}"
            2.threads = 22
        """

        os.environ.update(env)
        res = apply_config_from_string(config, "pg_pg", {})
        assert res["update_column"] == ""  # missing env var
        assert res["verbose"] is True
        assert res["threads"] == 4  # overwritten by pg_pg
        assert res["database1"] == {"driver": "postgresql", "user": "postgres", "password": "Password1"}
        assert res["database2"] == "postgresql://postgres:Password1@/"
        assert res["table1"] == "rating"
        assert res["table2"] == "rating_del1"
        assert res["threads1"] == 11
        assert res["threads2"] == 22

    def test_errors(self):
        def apply(config, run=None, kw=None):
            return apply_config_from_string(config, run, kw or {})

        ok_run = """
            [run.default]
            1.database = "postgresql://a"
            1.table = "t1"
            2.database = "postgresql://b"
            2.table = "t2"
        """
        res = apply(ok_run)
        assert res["database1"] == "postgresql://a" and res["table2"] == "t2"
        assert res["__conf__"]["table1"] == "t1"

        self.assertRaisesRegex(ConfigParseError, "Unknown option", apply, "bla = 1")
        self.assertRaisesRegex(ConfigParseError, "Could not find source #1", apply, "[run.default]\nx = 1")
        self.assertRaisesRegex(
            ConfigParseError, "missing attribute 'table'", apply, '[run.default]\n1.database = "postgresql://a"'
        )
        self.assertRaisesRegex(
            ConfigParseError,
            "Unexpected attributes",
            apply,
            '[run.default]\n1.database = "postgresql://a"\n1.table = "t"\n1.bla = 1',
        )
        self.assertRaisesRegex(
            ConfigParseError,
            "not found in list of databases",
            apply,
            '[run.default]\n1.database = "nope"\n1.table = "t"',
        )
        self.assertRaisesRegex(
            ConfigParseError,
            "did not specify a driver",
            apply,
            '[database.db]\nuser = "u"\n[run.default]\n1.database = "db"\n1.table = "t"',
        )

    def test_cli_args_override_run(self):
        config = """
            [run.default]
            verbose = true
        """
        kw = {"database1": "mysql://a", "table1": "t1", "database2": "mysql://b", "table2": None}
        self.assertRaisesRegex(
            ValueError, "Specified database1 but not table2", apply_config_from_string, config, None, kw
        )

        kw["table2"] = "t2"
        res = apply_config_from_string(config, None, kw)
        assert res["database1"] == "mysql://a"
        assert res["table2"] == "t2"
        assert res["verbose"] is True

    def test_apply_config_from_file(self):
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write(
                '[run.default]\n1.database = "duckdb://a"\n1.table = "t1"\n2.database = "duckdb://b"\n2.table = "t2"\n'
            )
        try:
            res = apply_config_from_file(f.name, None, {})
        finally:
            os.unlink(f.name)
        assert res["table1"] == "t1"
