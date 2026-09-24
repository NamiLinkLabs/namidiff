"""In-process CLI tests (click's CliRunner), so that namidiff.__main__ is measured by coverage.

Uses DuckDB files, relying on the shared connection cache of `namidiff.connect`.
"""

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta

from click.testing import CliRunner

from namidiff import __version__, connect
from namidiff.__main__ import main, diff_schemas, _remove_passwords_in_dict
from namidiff.sqeleton.queries import commit, table


class TestCLIInProcess(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.uri1 = f"duckdb://main@{cls.tmpdir}/a.duckdb"
        cls.uri2 = f"duckdb://main@{cls.tmpdir}/b.duckdb"
        cls.db1 = connect(cls.uri1)
        cls.db2 = connect(cls.uri2)

        now = datetime.now()
        schema = {"id": int, "updated": datetime, "text_comment": str, "other": str}
        rows = [(i, now - timedelta(days=i), f"row{i}", "x") for i in range(10)]
        for db in (cls.db1, cls.db2):
            src = table("src", schema=schema)
            db.query([src.create(), src.insert_rows(rows), commit])
        # dst differs from src by one extra row, in both databases
        for db in (cls.db1, cls.db2):
            dst = table("dst", schema=schema)
            db.query([dst.create(), dst.insert_rows(rows), dst.insert_row(100, now, "extra", "x"), commit])

        # Same column names but different types, for diff_schemas()
        other = table("other_types", schema={"id": int, "updated": datetime, "text_comment": int, "other": str})
        cls.db1.query([other.create(), other.insert_row(1, now, 1, "x"), commit])

    @classmethod
    def tearDownClass(cls):
        for db in (cls.db1, cls.db2):
            db.close()
        shutil.rmtree(cls.tmpdir)

    def run_cli(self, *args, **kw):
        return CliRunner().invoke(main, list(args), catch_exceptions=kw.pop("catch_exceptions", False), **kw)

    def _diff_lines(self, result):
        return [line for line in result.output.splitlines() if line[:1] in "+-" or line.startswith('["')]

    def test_version(self):
        res = self.run_cli("--version")
        assert res.output.strip() == f"v{__version__}"

    def test_help(self):
        res = self.run_cli("--help")
        assert "Cross-db diff" in res.output

    def test_arg_errors(self):
        with self.assertLogs(level="ERROR") as cm:
            self.run_cli(self.uri1, "src", "dst", "--limit", "1", "--stats")
            self.run_cli(self.uri1, "src", "dst", "-j", "abc")
            self.run_cli(self.uri1, "src", "dst", "-j", "0")
            self.run_cli("--limit", "1")
            self.run_cli(self.uri1, "src", "dst", "--min-age", "5x")
        msgs = "\n".join(cm.output)
        assert "Cannot specify a limit" in msgs
        assert "threads must be a number" in msgs
        assert "threads must be >= 1" in msgs
        assert "Databases not specified" in msgs
        assert "Error while parsing age expression" in msgs

    def test_joindiff_same_db(self):
        res = self.run_cli(self.uri1, "src", "dst", "-c", "text_comment", "-v")
        lines = self._diff_lines(res)
        assert lines == ["+ 100, extra"], res.output

    def test_joindiff_serial_with_options(self):
        res = self.run_cli(
            self.uri1, "src", "dst", "-j", "serial", "-a", "joindiff", "--assume-unique-key", "--sample-exclusive-rows"
        )
        assert self._diff_lines(res) == ["+ 100"], res.output

    def test_hashdiff_cross_db(self):
        res = self.run_cli(
            self.uri1,
            "src",
            self.uri2,
            "dst",
            "-k",
            "id",
            "-c",
            "text%",
            "-j",
            "2",
            "--bisection-factor",
            "2",
            "--bisection-threshold",
            "4",
            "--skip-sort-results",
        )
        assert self._diff_lines(res) == ["+ 100, extra"], res.output

    def test_limit(self):
        res = self.run_cli(self.uri1, "src", self.uri2, "dst", "-l", "1", "-a", "hashdiff")
        assert len(self._diff_lines(res)) == 1

    def test_json_output(self):
        res = self.run_cli(self.uri1, "src", "dst", "--json")
        assert [json.loads(line) for line in self._diff_lines(res)] == [["+", ["100"]]]

    def test_stats(self):
        res = self.run_cli(self.uri1, "src", self.uri2, "dst", "--stats")
        assert "exclusive" in res.output.lower(), res.output

        res = self.run_cli(self.uri1, "src", self.uri2, "dst", "--stats", "--json")
        stats = json.loads(res.output)
        assert stats["exclusive_B"] == 1 and stats["updated"] == 0, stats

    def test_min_max_age(self):
        # Only rows updated between 3.5 and 8.5 days ago
        res = self.run_cli(
            self.uri1, "src", self.uri2, "dst", "-t", "updated", "--min-age", "84h", "--max-age", "204h", "--stats"
        )
        assert res.exit_code == 0, res.output
        res = self.run_cli(self.uri1, "src", "dst", "-t", "updated", "--min-age", "1h")
        assert self._diff_lines(res) == [], res.output

    def test_column_not_found(self):
        with self.assertLogs(level="ERROR") as cm:
            self.run_cli(self.uri1, "src", "dst", "-c", "nope")
        assert "Column 'nope' not found" in "\n".join(cm.output)

        res = self.run_cli(self.uri1, "src", "dst", "-c", "nope", "-d", catch_exceptions=True)
        assert isinstance(res.exception, ValueError)

        # -i implies debug, so errors are raised too
        try:
            res = self.run_cli(self.uri1, "src", "dst", "-c", "nope", "-i", input="y\n" * 100, catch_exceptions=True)
        finally:
            self.db1.__dict__.pop("_interactive", None)  # Shared connection; restore the class default
        assert isinstance(res.exception, ValueError), res.exception

    def test_schema_mismatch_warning(self):
        with self.assertLogs(level="WARNING") as cm:
            self.run_cli(self.uri1, "src", "other_types", "-c", "text_comment", "-a", "hashdiff", "--case-sensitive")
        assert any("Schema mismatch in column 'text_comment'" in m for m in cm.output), cm.output

    def test_materialize(self):
        res = self.run_cli(self.uri1, "src", "dst", "-m", "diff_result_%t", "--materialize-all-rows")
        assert res.exit_code == 0, res.output
        tables = self.db1.query("select table_name from information_schema.tables", list)
        assert any(t.startswith("diff_result_") for (t,) in tables), tables

    def test_interactive(self):
        # Interactive mode asks for confirmation before every query
        try:
            res = self.run_cli(self.uri1, "src", "dst", "-i", "--bisection-threshold", "100", input="y\n" * 1000)
        finally:
            del self.db1._interactive  # Shared connection; restore the class default
        assert "+ 100" in res.output

    def test_conf(self):
        conf_path = os.path.join(self.tmpdir, "conf.toml")
        with open(conf_path, "w") as f:
            f.write(f"""
                [database.a]
                driver = "duckdb"
                filepath = "{self.tmpdir}/a.duckdb"
                dbname = "main"
                password = "secret"

                [run.default]
                1.database = "a"
                1.table = "src"
                2.database = "{self.uri2}"
                2.table = "dst"
                2.threads = 2
                """)
        with self.assertLogs(level="DEBUG") as cm:
            res = self.run_cli("--conf", conf_path, "-d", "-a", "hashdiff")
        assert "+ 100" in res.output, res.output
        conf_logs = [m for m in cm.output if "Applied run configuration" in m]
        assert conf_logs and "secret" not in conf_logs[0] and "******" in conf_logs[0], conf_logs


class TestCLIHelpers(unittest.TestCase):
    def test_remove_passwords_in_dict(self):
        d = {
            "password": "abc",
            "database1": "postgresql://user:pw@host/db",
            "nested": {"password": "xy", "other": 1},
        }
        _remove_passwords_in_dict(d)
        assert d == {
            "password": "***",
            "database1": "postgresql://user:***@host/db",
            "nested": {"password": "**", "other": 1},
        }

    def test_diff_schemas_missing_column(self):
        s1 = {"id": ("id", "int", None, None, None)}
        s2 = {"id": ("id", "int", None, None, None), "b": ("b", "int", None, None, None)}
        diff_schemas("t1", "t2", s1, s2, ("id", None))  # None is skipped
        self.assertRaisesRegex(ValueError, "not found in table 1", diff_schemas, "t1", "t2", s1, s2, ("b",))
        self.assertRaisesRegex(ValueError, "not found in table 2", diff_schemas, "t1", "t2", s2, s1, ("b",))
