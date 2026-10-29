import logging

from ..abcs.database_types import (
    Datetime,
    Timestamp,
    Float,
    Decimal,
    Integer,
    Text,
    TemporalType,
    FractionalType,
    ColType_UUID,
    Boolean,
    Date,
    StringType,
    String_Alphanum,
    ColType,
    DbPath,
)
from ..abcs.mixins import (
    AbstractMixin_MD5,
    AbstractMixin_NormalizeValue,
    AbstractMixin_Regex,
    AbstractMixin_RandomSample,
)
from .base import Mixin_OptimizerHints, ThreadedDatabase, import_helper, ConnectError, BaseDialect, Compilable
from .base import MD5_HEXDIGITS, CHECKSUM_HEXDIGITS, TIMESTAMP_PRECISION_POS, Mixin_Schema, Mixin_RandomSample
from ..queries.ast_classes import BinBoolOp

logger = logging.getLogger("database")

# Character sets whose code units are not bytes: cast them to utf8mb4 before comparing as binary,
# otherwise byte order is not code point order.
_MULTIBYTE_UNIT_CHARSETS = {"ucs2", "utf16", "utf16le", "utf32"}


def is_binary_collation(collation: str) -> bool:
    "True if the MySQL collation orders strings by code point (binary), i.e. like Python compares str."
    return collation == "binary" or collation.endswith("_bin")


@import_helper("mysql")
def import_mysql():
    import mysql.connector

    return mysql.connector


class Mixin_MD5(AbstractMixin_MD5):
    def md5_as_int(self, s: str) -> str:
        return f"cast(conv(substring(md5({s}), {1+MD5_HEXDIGITS-CHECKSUM_HEXDIGITS}), 16, 10) as unsigned)"


class Mixin_NormalizeValue(AbstractMixin_NormalizeValue):
    def normalize_timestamp(self, value: str, coltype: TemporalType) -> str:
        if self.timestamp_precision is not None:
            s = self.to_string(f"cast({value} as datetime(6))")
            return f"LEFT({s}, {TIMESTAMP_PRECISION_POS + self.timestamp_precision})"

        if coltype.rounds:
            return self.to_string(f"cast( cast({value} as datetime({coltype.precision})) as datetime(6))")

        s = self.to_string(f"cast({value} as datetime(6))")
        return f"RPAD(RPAD({s}, {TIMESTAMP_PRECISION_POS+coltype.precision}, '.'), {TIMESTAMP_PRECISION_POS+6}, '0')"

    def normalize_number(self, value: str, coltype: FractionalType) -> str:
        if isinstance(coltype, Float):
            return self.to_string(f"cast(round({value}, {coltype.precision}) as decimal(38, {coltype.precision}))")
        return self.to_string(f"cast({value} as decimal(38, {coltype.precision}))")

    def normalize_uuid(self, value: str, coltype: ColType_UUID) -> str:
        return f"TRIM(CAST({value} AS char))"

    def normalize_text(self, value: str, coltype: StringType) -> str:
        if self.empty_string_as_null:
            return f"NULLIF(cast({value} as char), '')"
        return self.to_string(value)


class Mixin_Regex(AbstractMixin_Regex):
    def test_regex(self, string: Compilable, pattern: Compilable) -> Compilable:
        return BinBoolOp("REGEXP", [string, pattern])


class Dialect(BaseDialect, Mixin_Schema, Mixin_OptimizerHints):
    name = "MySQL"
    SUPPORTS_TIMESTAMP_PRECISION = True
    ROUNDS_ON_PREC_LOSS = True
    SUPPORTS_PRIMARY_KEY = True
    SUPPORTS_INDEXES = True

    TYPE_CLASSES = {
        # Dates
        "datetime": Datetime,
        "timestamp": Timestamp,
        "date": Date,
        # Numbers
        "double": Float,
        "float": Float,
        "decimal": Decimal,
        "int": Integer,
        "bigint": Integer,
        "smallint": Integer,
        "tinyint": Integer,
        "mediumint": Integer,
        # Text
        "varchar": Text,
        "char": Text,
        "varbinary": Text,
        "binary": Text,
        "text": Text,
        "mediumtext": Text,
        "longtext": Text,
        "tinytext": Text,
        # Boolean
        "boolean": Boolean,
    }
    MIXINS = {Mixin_Schema, Mixin_MD5, Mixin_NormalizeValue, Mixin_RandomSample, Mixin_Regex}

    def quote(self, s: str):
        return f"`{s}`"

    def to_string(self, s: str):
        return f"cast({s} as char)"

    def is_distinct_from(self, a: str, b: str) -> str:
        return f"not ({a} <=> {b})"

    def random(self) -> str:
        return "RAND()"

    def type_repr(self, t) -> str:
        if isinstance(t, type):
            try:
                return {
                    str: "VARCHAR(1024)",
                }[t]
            except KeyError:
                pass
        return super().type_repr(t)

    def explain_as_text(self, query: str) -> str:
        return f"EXPLAIN FORMAT=TREE {query}"

    def optimizer_hints(self, s: str):
        return f"/*+ {s} */ "

    def set_timezone_to_utc(self) -> str:
        return "SET @@session.time_zone='+00:00'"

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
        if isinstance(coltype, StringType):
            if collation_name is None and type_repr in ("binary", "varbinary"):
                # information_schema reports no collation for binary strings; they compare by bytes
                collation_name = "binary"
            coltype = coltype.replace(collation=collation_name)
        return coltype

    def collate_key(self, value: str, coltype: ColType) -> str:
        """Order string keys by code point, like Python and the other databases do.

        MySQL compares strings according to the column's collation. Case-insensitive and accent-insensitive
        collations (the defaults: utf8mb4_0900_ai_ci, utf8mb4_general_ci, latin1_swedish_ci, ...) put
        'a' before 'B', while Python, Snowflake, Postgres (C locale), BigQuery, etc. put 'B' before 'a'.
        Bisecting an alphanumeric key range with such a collation assigns rows to different segments on
        the two sides of the diff, which reports rows as missing or extra when they are not.

        Columns that already use a binary collation (``*_bin``, ``binary``) are left alone, so their index
        stays usable for range scans. For the others, the key is compared as ``cast(key as binary)``:
        byte order, NO PAD, which matches Python exactly. MySQL cannot use the column's index for that,
        so every segment query scans the table. See docs/supported-databases.md for how to add a
        functional index when that is too slow.

        UUID keys are not affected: namidiff compares them numerically, and the case-insensitive
        collations order hex digits the same way.
        """
        if not isinstance(coltype, String_Alphanum):
            return value

        collation = coltype.collation
        if collation is not None and is_binary_collation(collation):
            return value

        charset = collation.split("_", 1)[0] if collation else None
        if charset in _MULTIBYTE_UNIT_CHARSETS:
            expr = f"cast(convert({value} using utf8mb4) as binary)"
        else:
            expr = f"cast({value} as binary)"
        self._warn_key_not_sargable(value, collation, expr)
        return expr

    def _warn_key_not_sargable(self, column: str, collation: str, expr: str) -> None:
        "Warn once per column that MySQL cannot use the column's index for the key range predicates."
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
            f"MySQL key column {column} {reason}. namidiff compares its key ranges as {expr}, "
            f"which MySQL cannot use an index for, so every segment query scans the table. "
            f"If the diff is slow on this table, add a functional index (MySQL 8.0.13+): "
            f"ALTER TABLE <table> ADD INDEX (({expr}))"
        )


class MySQL(ThreadedDatabase):
    dialect = Dialect()
    # String keys work with any collation: collate_key() forces a binary order for non-binary collations.
    SUPPORTS_ALPHANUMS = True
    SUPPORTS_UNIQUE_CONSTAINT = True
    CONNECT_URI_HELP = "mysql://<user>:<password>@<host>/<database>"
    CONNECT_URI_PARAMS = ["database?"]

    def __init__(self, *, thread_count, **kw):
        self._args = kw

        super().__init__(thread_count=thread_count)

        # In MySQL schema and database are synonymous
        try:
            self.default_schema = kw["database"]
        except KeyError:
            raise ValueError("MySQL URL must specify a database")

    def select_table_schema(self, path: DbPath) -> str:
        """Like the default, plus the column collation, which parse_type() keeps on string types."""
        schema, name = self._normalize_table_path(path)

        return (
            "SELECT column_name, data_type, datetime_precision, numeric_precision, numeric_scale, collation_name "
            "FROM information_schema.columns "
            f"WHERE table_name = '{name}' AND table_schema = '{schema}'"
        )

    def create_connection(self):
        mysql = import_mysql()
        try:
            return mysql.connect(charset="utf8", use_unicode=True, **self._args)
        except mysql.Error as e:
            if e.errno == mysql.errorcode.ER_ACCESS_DENIED_ERROR:
                raise ConnectError("Bad user name or password") from e
            elif e.errno == mysql.errorcode.ER_BAD_DB_ERROR:
                raise ConnectError("Database does not exist") from e
            raise ConnectError(*e.args) from e
