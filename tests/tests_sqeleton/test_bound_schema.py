import unittest

from namidiff.sqeleton import databases as db
from namidiff.sqeleton.bound_exprs import BoundNode, BoundTable, bound_table
from namidiff.sqeleton.queries import commit, table, this
from namidiff.sqeleton.query_utils import append_to_table, drop_table
from namidiff.sqeleton.schema import Options, TableType, _Field, _Schema, create_schema, options
from namidiff.sqeleton.utils import CaseInsensitiveDict, CaseSensitiveDict

from .common import DbTestCase, get_conn, make_test_each_database_in_list

test_each_database = make_test_each_database_in_list([db.DuckDB, db.PostgreSQL, db.MySQL, db.Oracle])


class TestSchema(unittest.TestCase):
    def test_make_from_dict(self):
        s = _Schema.make({"a": int})
        assert isinstance(s, CaseSensitiveDict) and s["a"] is int

        ci = CaseInsensitiveDict({"A": int})
        assert _Schema.make(ci) is ci

    def test_make_from_table_type(self):
        class Person(TableType):
            id: int = options(primary_key=True)
            name: str = None
            age: int = 5

        assert TableType.is_superclass(Person)
        assert not TableType.is_superclass(Person())
        s = _Schema.make(Person)
        assert s["id"] == _Field(int, Options(primary_key=True))
        assert s["name"] is str
        assert s["age"] == _Field(int, Options(default=5))

    def test_create_schema(self):
        raw = {"Id": int, "id": str}
        assert isinstance(create_schema(None, ("t",), raw, True), CaseSensitiveDict)
        with self.assertLogs("schema", level="WARNING") as cm:
            s = create_schema(None, ("t",), raw, False)
        assert isinstance(s, CaseInsensitiveDict)
        assert "Ambiguous schema" in cm.output[0]


class TestBoundExprs(unittest.TestCase):
    def setUp(self):
        self.conn = get_conn(db.DuckDB)
        self.conn.query([table("bound_t", schema={"id": int, "name": str}).create(), commit])
        self.conn.query([table("bound_t").insert_rows([(1, "a"), (2, "b")]), commit])

    def tearDown(self):
        drop_table(self.conn, ("bound_t",))

    def test_bound_table(self):
        t = self.conn.table("bound_t")
        assert isinstance(t, BoundTable)
        assert t.schema is None

        # Methods of the underlying node are bound to the database
        q = t.select(this.id).order_by(this.id)
        assert isinstance(q, BoundNode)
        assert q.query() == [(1,), (2,)]

        # Non-method attributes are passed through
        assert t.path == ("bound_t",)

        t2 = t.query_schema(case_sensitive=False)
        assert set(t2.schema) == {"id", "name"}
        assert t2.query_schema() is t2  # Already has a schema

        count = t2.count()
        assert count.type == t2.node.count().type
        assert count.query(int) == 2

    def test_bind(self):
        node = table("bound_t").count()
        bound = node.bind(self.conn)
        assert isinstance(bound, BoundNode)
        assert bound.query(int) == 2

        bt = bound_table(self.conn, "bound_t", schema={"id": int})
        assert bt.schema["id"] is int

    def test_embed_bound_node_in_query(self):
        bound = table("bound_t").count().bind(self.conn)
        q = table("bound_t").alias("o").select(this.id, bound).order_by(this.id)
        assert self.conn.compile(q) == (
            'SELECT "id", (SELECT count(*) FROM "bound_t") FROM "bound_t" "o" ORDER BY "id"'
        )
        assert self.conn.query(q, list) == [(1, 2), (2, 2)]


@test_each_database
class TestQueryUtils(DbTestCase):
    def test_drop_and_append(self):
        src_path = self.connection.parse_table_name(f"qu_src_{self.table1_name}")
        dst_path = self.connection.parse_table_name(f"qu_dst_{self.table1_name}")
        src = table(src_path, schema={"id": int})
        try:
            self.connection.query([src.create(), src.insert_rows([(1,), (2,)]), commit])

            expr = src.select(this.id)
            append_to_table(self.connection, dst_path, expr)  # Creates the table
            append_to_table(self.connection, dst_path, expr)  # Appends to the existing table
            assert self.connection.query(table(dst_path).count(), int) == 4
        finally:
            drop_table(self.connection, dst_path)
            drop_table(self.connection, dst_path)  # Dropping a missing table is not an error
            drop_table(self.connection, src_path)
