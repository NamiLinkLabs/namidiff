import logging
import threading
from typing import Dict, List, Optional

from ..abcs.database_types import (
    ColType,
    DbPath,
    String_Alphanum,
    Timestamp,
    TimestampTZ,
    Float,
    Decimal,
    Integer,
    TemporalType,
    Native_UUID,
    Text,
    FractionalType,
    Boolean,
    Date,
    StringType,
)
from ..abcs.mixins import AbstractMixin_MD5, AbstractMixin_NormalizeValue
from .base import BaseDialect, ThreadedDatabase, import_helper, ConnectError, Mixin_Schema
from .base import MD5_HEXDIGITS, CHECKSUM_HEXDIGITS, _CHECKSUM_BITSIZE, TIMESTAMP_PRECISION_POS, Mixin_RandomSample
from ..schema import _Field

SESSION_TIME_ZONE = None  # Changed by the tests

logger = logging.getLogger("database")

# Collations that order strings by code point (like Python compares str), by name as information_schema reports them.
# "C"/"POSIX" compare bytes, and so do C.UTF-8 locales for the ASCII keys namidiff accepts. ucs_basic is the SQL
# standard name for it (libc C before PostgreSQL 17, builtin C since). pg_c_utf8 and pg_unicode_fast are the
# builtin provider's (PostgreSQL 17+, 18+). Any other collation (en_US.UTF-8, en-US-x-icu, ...) is linguistic.
CODE_POINT_COLLATIONS = {"C", "POSIX", "C.UTF-8", "C.utf8", "ucs_basic", "pg_c_utf8", "pg_unicode_fast"}

# Builtin provider locales (PostgreSQL 17+), all in code point order, as the name of their collation
_BUILTIN_LOCALE_COLLATIONS = {"C": "C", "C.UTF-8": "pg_c_utf8", "PG_UNICODE_FAST": "pg_unicode_fast"}


@import_helper("postgresql")
def import_postgresql():
    import psycopg2
    import psycopg2.extras

    psycopg2.extensions.set_wait_callback(psycopg2.extras.wait_select)
    return psycopg2


class Mixin_MD5(AbstractMixin_MD5):
    def md5_as_int(self, s: str) -> str:
        return f"('x' || substring(md5({s}), {1+MD5_HEXDIGITS-CHECKSUM_HEXDIGITS}))::bit({_CHECKSUM_BITSIZE})::bigint"


class Mixin_NormalizeValue(AbstractMixin_NormalizeValue):
    def normalize_timestamp(self, value: str, coltype: TemporalType) -> str:
        if self.timestamp_precision is not None:
            p = self.timestamp_precision
            return f"LEFT(to_char({value}::timestamp(6), 'YYYY-mm-dd HH24:MI:SS.US'), {TIMESTAMP_PRECISION_POS+p})"

        if coltype.rounds:
            return f"to_char({value}::timestamp({coltype.precision}), 'YYYY-mm-dd HH24:MI:SS.US')"

        timestamp6 = f"to_char({value}::timestamp(6), 'YYYY-mm-dd HH24:MI:SS.US')"
        return (
            f"RPAD(LEFT({timestamp6}, {TIMESTAMP_PRECISION_POS+coltype.precision}), {TIMESTAMP_PRECISION_POS+6}, '0')"
        )

    def normalize_number(self, value: str, coltype: FractionalType) -> str:
        return self.to_string(f"{value}::decimal(38, {coltype.precision})")

    def normalize_boolean(self, value: str, _coltype: Boolean) -> str:
        return self.to_string(f"{value}::int")

    def normalize_text(self, value: str, coltype: StringType) -> str:
        if self.empty_string_as_null:
            return f"NULLIF({value}::varchar, '')"
        return self.to_string(value)


class PostgresqlDialect(BaseDialect, Mixin_Schema):
    name = "PostgreSQL"
    SUPPORTS_TIMESTAMP_PRECISION = True
    ROUNDS_ON_PREC_LOSS = True
    SUPPORTS_PRIMARY_KEY = True
    SUPPORTS_INDEXES = True
    MIXINS = {Mixin_Schema, Mixin_MD5, Mixin_NormalizeValue, Mixin_RandomSample}

    TYPE_CLASSES = {
        # Timestamps
        "timestamp with time zone": TimestampTZ,
        "timestamp without time zone": Timestamp,
        "timestamp": Timestamp,
        "date": Date,
        # Numbers
        "double precision": Float,
        "real": Float,
        "decimal": Decimal,
        "smallint": Integer,
        "integer": Integer,
        "numeric": Decimal,
        "bigint": Integer,
        # Text
        "character": Text,
        "character varying": Text,
        "varchar": Text,
        "text": Text,
        # UUID
        "uuid": Native_UUID,
        # Boolean
        "boolean": Boolean,
    }

    def quote(self, s: str):
        return f'"{s}"'

    def to_string(self, s: str):
        return f"{s}::varchar"

    def concat(self, items: List[str]) -> str:
        # Postgres does not allow functions that have more that have more than 100 arguments.
        # When using the concat function, this limits comparisons to less than 50 fields.
        # Using || for concat fixes this.
        joined_exprs = " || ".join(items)
        return f"({joined_exprs})"

    def _convert_db_precision_to_digits(self, p: int) -> int:
        # Subtracting 2 due to wierd precision issues in PostgreSQL
        return super()._convert_db_precision_to_digits(p) - 2

    def set_timezone_to_utc(self) -> str:
        return "SET TIME ZONE 'UTC'"

    def current_timestamp(self) -> str:
        return "current_timestamp"

    def parse_type(
        self,
        table_path: DbPath,
        col_name: str,
        type_repr: str,
        datetime_precision: int = None,
        numeric_precision: int = None,
        numeric_scale: int = None,
        collation_name: str = None,
    ) -> ColType:
        coltype = super().parse_type(
            table_path, col_name, type_repr, datetime_precision, numeric_precision, numeric_scale
        )
        if isinstance(coltype, StringType) and collation_name is not None:
            coltype = coltype.replace(collation=collation_name)
        return coltype

    def collate_key(self, value: str, coltype: ColType) -> str:
        """Order string keys by code point, like Python and the other databases do.

        PostgreSQL compares strings according to the column's collation, which defaults to the database's.
        The C locale orders by code point, but many installs and managed services default to a linguistic one
        (en_US.UTF-8, or an ICU locale), which puts 'a1' before 'B', and ignores punctuation like '_' and '-'
        at first. Bisecting an alphanumeric key range in such a collation assigns rows to different segments
        on the two sides of the diff, which reports rows as missing or extra when they are not.

        Columns whose collation already orders by code point (CODE_POINT_COLLATIONS) are left alone, so their
        index stays usable for range scans. The others are compared as ``key COLLATE "C"``. That changes
        nothing in the database, but the planner can only use an index built with the same collation for it,
        e.g. ``CREATE INDEX ON t (key COLLATE "C")``. Otherwise every segment query scans the table.

        UUID keys are not affected: they are compared numerically (native ``uuid``), or their hex digits
        order the same in any collation.
        """
        if not isinstance(coltype, String_Alphanum):
            return value

        collation = coltype.collation
        if collation in CODE_POINT_COLLATIONS:
            return value

        expr = f'({value} COLLATE "C")'
        self._warn_key_not_sargable(value, collation, expr)
        return expr

    def _warn_key_not_sargable(self, column: str, collation: Optional[str], expr: str) -> None:
        "Warn once per key column (and dialect) that the column's index cannot be used for its key ranges."
        warned = self.__dict__.setdefault("_collated_keys_warned", set())
        if (column, collation) in warned:
            return
        warned.add((column, collation))

        reason = (
            f"has collation '{collation}', which does not order strings by code point"
            if collation
            else "has an unknown collation"
        )
        logger.warning(
            f"{self.name} key column {column} {reason}. namidiff compares its key ranges as {expr}, "
            "which cannot use an index of the column's own collation, so every segment query scans the table. "
            f'If the diff is too slow, add an index in that collation: CREATE INDEX ON <table> ({column} COLLATE "C")'
        )

    def type_repr(self, t) -> str:
        if isinstance(t, _Field):
            if t.options.auto:
                assert t.type is int
                return "SERIAL"
            else:
                t = t.type

        if isinstance(t, TimestampTZ):
            return f"timestamp ({t.precision}) with time zone"
        elif t in (dict, list):
            return "json"
        return super().type_repr(t)


class PostgreSQL(ThreadedDatabase):
    dialect = PostgresqlDialect()
    SUPPORTS_UNIQUE_CONSTAINT = True
    CONNECT_URI_HELP = "postgresql://<user>:<password>@<host>/<database>"
    CONNECT_URI_PARAMS = ["database?"]

    default_schema = "public"

    def __init__(self, *, thread_count, **kw):
        self._args = kw

        super().__init__(thread_count=thread_count)

        self._database_collation_lock = threading.Lock()
        self._database_collation_name = None

    def create_connection(self):
        if not self._args:
            self._args["host"] = None  # psycopg2 requires 1+ arguments

        pg = import_postgresql()
        try:
            c = pg.connect(**self._args)
            if SESSION_TIME_ZONE:
                c.cursor().execute(f"SET TIME ZONE '{SESSION_TIME_ZONE}'")
            return c
        except pg.OperationalError as e:
            raise ConnectError(*e.args) from e

    def select_table_schema(self, path: DbPath) -> str:
        database, schema, table = self._normalize_table_path(path)

        info_schema_path = ["information_schema", "columns"]
        if database:
            info_schema_path.insert(0, database)

        return (
            "SELECT column_name, data_type, datetime_precision, numeric_precision, numeric_scale, collation_name "
            f"FROM {'.'.join(info_schema_path)} "
            f"WHERE table_name = '{table}' AND table_schema = '{schema}'"
        )

    def query_table_schema(self, path: DbPath) -> Dict[str, tuple]:
        """Like the default, plus the collation of string columns, which parse_type() keeps on StringType.

        information_schema reports NULL as collation_name for columns in the database's default collation,
        so those get the database's (see _database_collation()).
        """
        rows = super().query_table_schema(path)
        for name, row in rows.items():
            # Subclasses (Redshift) may not select the collation
            if len(row) > 5 and row[5] is None and self._is_string_type(row[1]):
                rows[name] = (*row[:5], self._database_collation())
        return rows

    def _is_string_type(self, type_repr: str) -> bool:
        cls = self.dialect._parse_type_repr(type_repr)
        return cls is not None and issubclass(cls, StringType)

    def _database_collation(self) -> str:
        """The name of the current database's default collation, as information_schema would name it.
        Queried once per connection.

        pg_database has the provider since PostgreSQL 15, and the ICU or builtin locale in daticulocale (15, 16)
        or datlocale (17+). Read as JSON, so that one query works on every version. datcollate is always set,
        but only used for the libc provider: ICU databases (PostgreSQL 15+) often keep 'C' there.
        """
        with self._database_collation_lock:
            if self._database_collation_name is None:
                ((row,),) = self.query("SELECT to_jsonb(d) FROM pg_database d WHERE datname = current_database()", list)
                provider = row.get("datlocprovider", "c")
                locale = row.get("datlocale") or row.get("daticulocale")
                if provider == "i":
                    # Like PostgreSQL names its ICU collations ('en-US-x-icu'), so it never matches the code point set
                    name = f"{locale}-x-icu"
                elif provider == "b":
                    name = _BUILTIN_LOCALE_COLLATIONS.get(locale, locale)
                else:
                    name = row["datcollate"]
                self._database_collation_name = name
            return self._database_collation_name

    def select_table_unique_columns(self, path: DbPath) -> str:
        database, schema, table = self._normalize_table_path(path)

        info_schema_path = ["information_schema", "key_column_usage"]
        if database:
            info_schema_path.insert(0, database)

        return (
            "SELECT column_name "
            f"FROM {'.'.join(info_schema_path)} "
            f"WHERE table_name = '{table}' AND table_schema = '{schema}'"
        )

    def _normalize_table_path(self, path: DbPath) -> DbPath:
        if len(path) == 1:
            return None, self.default_schema, path[0]
        elif len(path) == 2:
            return None, path[0], path[1]
        elif len(path) == 3:
            return path

        raise ValueError(
            f"{self.name}: Bad table path for {self}: '{'.'.join(path)}'. Expected format: table, schema.table, or database.schema.table"
        )
