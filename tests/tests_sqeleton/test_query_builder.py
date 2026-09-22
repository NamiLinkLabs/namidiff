"""Query-builder tests (ast_classes / compiler / api), executed against in-memory DuckDB where possible."""

import unittest
from datetime import datetime
from typing import List

from namidiff.sqeleton import databases as db
from namidiff.sqeleton.queries import SKIP, Compiler, CompileError, code, commit, table, this
from namidiff.sqeleton.queries.api import (
    and_,
    avg,
    coalesce,
    exists,
    insert_rows_in_batches,
    max_,
    min_,
    or_,
    sum_,
    when,
)
from namidiff.sqeleton.queries.ast_classes import (
    Desc,
    ITable,
    Param,
    QB_TypeError,
    QueryBuilderError,
    TablePath,
    _ResolveColumn,
)
from namidiff.sqeleton.queries.base import SKIP as BASE_SKIP, args_as_tuple
from namidiff.sqeleton.queries.compiler import cv_params
from namidiff.sqeleton.query_utils import drop_table

from .common import CONN_STRINGS, get_conn


class TestQueryBuilderDuckDB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = get_conn(db.DuckDB)
        cls.t = table("qb_t", schema={"id": int, "grp": str, "val": int})
        cls.u = table("qb_u", schema={"id": int, "name": str})
        cls.conn.query([cls.t.create(), cls.u.create(), commit])
        cls.conn.query(cls.t.insert_rows([(1, "a", 10), (2, "a", 20), (3, "b", 30), (4, "b", None)]))
        cls.conn.query(cls.u.insert_rows([(1, "one"), (3, "three")]))

    @classmethod
    def tearDownClass(cls):
        drop_table(cls.conn, ("qb_t",))
        drop_table(cls.conn, ("qb_u",))

    def q(self, expr, res_type=list):
        return self.conn.query(expr, res_type)

    def ids(self, expr):
        return [i for (i,) in self.q(expr.order_by(this.id))]

    def test_operators(self):
        t = self.t
        assert self.ids(t.where(this.id - 1 == 0).select(this.id)) == [1]
        assert self.ids(t.where(5 - this.id == 1).select(this.id)) == [4]
        assert self.ids(t.where(this.id * 2 == 4).select(this.id)) == [2]
        assert self.ids(t.where(this.val / 10 == 3).select(this.id)) == [3]
        assert self.ids(t.where(this.id != 1).select(this.id)) == [2, 3, 4]
        assert self.ids(t.where(this.id <= 2).select(this.id)) == [1, 2]
        assert self.ids(t.where((this.id >= 2) & (this.id < 4)).select(this.id)) == [2, 3]
        assert self.ids(t.where((this.id == 2).not_()).select(this.id)) == [1, 3, 4]
        assert self.ids(t.where(this.grp.ilike("A")).select(this.id)) == [1, 2]
        assert self.ids(t.where(this.id.in_(1, 3)).select(this.id)) == [1, 3]
        assert self.ids(t.where(this.id.in_(self.u.select(this.id))).select(this.id)) == [1, 3]
        assert t.select(this.id).where(this.id.in_()) is not None  # Empty IN is a constant False
        assert self.q(t.where(this.id.in_()).select(this.id)) == []
        assert self.ids(t.where(this.val == None).select(this.id)) == [4]
        assert self.ids(t.where(this.val != None).select(this.id)) == [1, 2, 3]
        assert self.ids(t.where(or_(this.id == 1, this.id == 4)).select(this.id)) == [1, 4]
        assert self.ids(t.where(and_(this.id > 1)).select(this.id)) == [2, 3, 4]
        assert self.ids(t.where(or_(this.id == 2)).select(this.id)) == [2]

    def test_aggregates(self):
        t = self.t
        assert self.q(t.select(sum_(this.val), avg(this.val), min_(this.val), max_(this.val)), tuple) == (
            60,
            20.0,
            10,
            30,
        )
        assert self.q(t.select(this.val.max(), this.val.min(), this.grp.count(distinct=True)), tuple) == (30, 10, 2)
        assert self.q(t.select(this.val.sum()), int) == 60

    def test_group_by_having(self):
        t = self.t
        q = t.group_by(this.grp).agg(total=this.val.sum()).having(this.val.max() > 25)
        assert self.q(q) == [("b", 30)]
        assert set(q.schema) == {"grp", "total"}
        assert q.having() is q  # No expressions -> unchanged

        # group_by on a select
        q = t.select(this.grp, this.val).group_by(this.grp).agg(this.val.count())
        assert sorted(self.q(q)) == [("a", 2), ("b", 1)]

    def test_order_limit_desc(self):
        t = self.t
        assert t.order_by() is t and t.order_by(SKIP) is t
        assert t.limit(SKIP) is t
        assert self.q(t.select(this.id).order_by(Desc(this.id)).limit(2)) == [(4,), (3,)]

    def test_join(self):
        t, u = self.t.alias("a"), self.u.alias("b")
        j = t.join(u).on("id")
        assert j.on() is j
        rows = self.q(j.select(t["id"], u["name"]).order_by(t["id"]))
        assert rows == [(1, "one"), (3, "three")]

        # A second select on an already selected join
        j2 = j.select(t["id"], u["name"]).select(this.name)
        assert sorted(self.q(j2)) == [("one",), ("three",)]

        with self.assertRaises(TypeError):
            j.select(object())

    def test_table_getitem(self):
        t = self.t
        assert [c.name for c in t[["id", "grp"]]] == ["id", "grp"]
        assert [c.name for c in t[...]] == ["id", "grp", "val"]
        with self.assertRaises(TypeError):
            t[1]

    def test_case_when(self):
        t = table("qb_t")  # No schema, so unnamed expressions can be selected
        expr = when(this.val > 15).then("big").else_("small")
        assert self.q(t.select(this.id, expr).order_by(this.id)) == [(1, "small"), (2, "big"), (3, "big"), (4, "small")]

        expr = when(this.val > 25, this.grp == "b").then("b-big").when(this.val > 15).then("big").else_("small")
        assert self.q(t.select(expr).where(this.id == 3), str) == "b-big"
        assert expr.type is str

        with self.assertRaises(QueryBuilderError):
            when()
        with self.assertRaises(QueryBuilderError):
            when(this.val > 1).then(1).when()
        with self.assertRaises(QueryBuilderError):
            when(this.val > 1).then(1).else_(2).else_(3)
        with self.assertRaises(QB_TypeError):
            when(this.val > 1).then(1).else_("x").type

    def test_exists_and_cast(self):
        t, u = self.t, self.u
        q = t.select(this.id).where(exists(u.where(u["id"] == 3).select(u["id"])))
        assert len(self.q(q)) == 4
        assert self.q(table("qb_t").select(this.id.cast_to(code("VARCHAR"))).where(this.id == 1), str) == "1"

    def test_table_ops(self):
        t, u = self.t, self.u
        assert sorted(self.q(t.select(this.id).union(u.select(this.id)))) == [(1,), (2,), (3,), (4,)]
        assert len(self.q(t.select(this.id).union_all(u.select(this.id)))) == 6
        assert sorted(self.q(t.select(this.id).minus(u.select(this.id)))) == [(2,), (4,)]
        assert sorted(self.q(t.select(this.id).intersect(u.select(this.id)))) == [(1,), (3,)]

    def test_statements(self):
        tmp = table("qb_tmp", schema={"id": int, "name": str})
        try:
            self.conn.query(tmp.create())
            assert tmp.insert_rows([]) is SKIP
            self.conn.query(tmp.insert_rows([{"id": 1, "name": "x"}, {"id": 2, "name": "y"}]))
            self.conn.query(tmp.insert_rows([{"id": 3, "name": "z", "extra": 0}], columns=["id", "name"]))
            self.conn.query(tmp.insert_row(id=4, name="w"))
            self.conn.query(tmp.insert_expr(tmp.where(this.id == 1)))  # TablePath exprs are converted to select
            with self.assertRaises(ValueError):
                tmp.insert_rows([{"id": 1}], columns=["nope"])
            with self.assertRaises(ValueError):
                tmp.insert_row()
            with self.assertRaises(ValueError):
                tmp.insert_row(1, name="x")

            assert self.q(tmp.count(), int) == 5
            self.conn.query(tmp.update_fields(this.id == 1, name="updated"))
            assert self.q(tmp.where(this.name == "updated").count(), int) == 2
            self.conn.query(tmp.delete_rows(this.id == 1))
            assert self.q(tmp.count(), int) == 3

            # RETURNING
            rows = self.q(tmp.delete_rows(this.id == 4).returning(this.id))
            assert rows == [(4,)]
            with self.assertRaises(ValueError):
                tmp.delete_rows(this.id == 4).returning(this.id).returning(this.id)
            stmt = tmp.update_fields(name="all")
            assert stmt.returning() is stmt and stmt.type is None
            assert self.q(stmt.returning(this.name)) == [("all",), ("all",)]

            # delete_rows() without conditions truncates
            self.conn.query(tmp.delete_rows())
            assert self.q(tmp.count(), int) == 0

            copy = table("qb_tmp2")
            self.conn.query(copy.create(tmp))  # Create from a TablePath
            drop_table(self.conn, ("qb_tmp2",))
            with self.assertRaisesRegex(ValueError, "Either schema or source table needed"):
                table("qb_x").create()
        finally:
            drop_table(self.conn, ("qb_tmp",))

    def test_insert_rows_in_batches(self):
        tmp = table("qb_batch", schema={"id": int})
        try:
            self.conn.query(tmp.create())
            insert_rows_in_batches(self.conn, tmp, [(i,) for i in range(10)], batch_size=3)
            assert self.q(tmp.count(), int) == 10
        finally:
            drop_table(self.conn, ("qb_batch",))

    def test_param(self):
        p = Param("tbl")
        assert p.source_table is p
        c = Compiler(self.conn)
        assert c.compile(table("qb_t").select(this.id).where(this.id == Param("x")), {"x": 3}).endswith('("id" = 3)')

    def test_code_and_postfix(self):
        raw = table("qb_t")
        q = raw.select(raw["id"], code("{x} + 1", x=raw["id"]))
        assert self.q(q.order_by(this.id).limit(1)) == [(1, 2)]
        c = Compiler(self.conn)
        sel = self.t.select(this.id).replace(postfix=" LIMIT 1")
        assert c.compile(sel).endswith(" LIMIT 1")

    def test_select_make(self):
        s = self.t.where(this.id > 1)
        s2 = s.where(this.id < 4)
        assert len(s2.where_exprs) == 2  # merged
        with self.assertRaises(ValueError):
            s.order_by(this.id).order_by(this.val)
        # A limit prevents merging
        s3 = s.limit(1).where(this.id > 2)
        assert s3.table.limit_expr == 1
        # Distinct can't be merged into a non-distinct select
        d = self.t.select(this.grp, distinct=True).select(this.grp, distinct=False)
        assert d.table.distinct
        assert sorted(self.q(self.t.select(this.grp, distinct=True))) == [("a",), ("b",)]

    def test_misc_ast(self):
        tp = table("qb_t")
        assert repr(tp) == "TablePath(('qb_t',))"
        assert repr(self.t) == "TablePath(('qb_t',), schema=<3 cols>)"
        assert table(tp).path == ("qb_t",)
        with self.assertRaises(TypeError):
            table(1)
        with self.assertRaises(QueryBuilderError):
            table("qb_noschema")["x"].type  # noqa  # Column type needs a schema
        assert (self.t["id"] + self.t["val"]).type is int

        r = _ResolveColumn("x")
        with self.assertRaises(QueryBuilderError):
            r._get_resolved()
        r.resolve(this.id)
        with self.assertRaises(QueryBuilderError):
            r.resolve(this.id)

        assert args_as_tuple(((x for x in [1, 2]),)) == (1, 2)
        assert args_as_tuple((1, 2)) == (1, 2)
        assert repr(BASE_SKIP) == "SKIP"
        import copy

        assert copy.deepcopy(BASE_SKIP) is BASE_SKIP


class TestCompilerErrors(unittest.TestCase):
    def setUp(self):
        self.conn = get_conn(db.DuckDB)

    def test_bytes_and_dataclass_params(self):
        c = Compiler(self.conn)
        assert c._add_as_param(b"abc") in ("b'abc'", "'abc'")

    def test_regex_not_supported(self):
        with self.assertRaises(NotImplementedError):
            self.conn.compile(table("t").select(this.a.test_regex("x.*")))

    def test_duplicate_alias(self):
        a = table("t1").alias("x")
        b = table("t2").alias("x")
        with self.assertRaises(ValueError):
            self.conn.compile(a.join(b).on(a["id"] == b["id"]).select(a["id"]))


@unittest.skipUnless(db.MySQL in CONN_STRINGS, "Requires MySQL")
class TestRegexMySQL(unittest.TestCase):
    def test_regex(self):
        from namidiff import sqeleton
        from namidiff.sqeleton.abcs.mixins import AbstractMixin_Regex

        conn = sqeleton.connect.for_databases("mysql").load_mixins(AbstractMixin_Regex)(
            CONN_STRINGS[db.MySQL], shared=False
        )
        rows = conn.query(
            table("information_schema", "tables")
            .select(this.table_name)
            .where(this.table_name.test_regex("^TABLES$"))
            .limit(1),
            list,
        )
        assert rows == [("TABLES",)], rows
