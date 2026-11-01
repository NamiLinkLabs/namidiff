import logging
import threading
from typing import List, Optional

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

# NO PAD collations that order utf8mb4 strings by code point, in order of preference.
# MySQL 8.0.17+ has utf8mb4_0900_bin, MariaDB 10.2+ has utf8mb4_nopad_bin. MySQL 5.7 has neither.
NOPAD_BINARY_COLLATIONS = ("utf8mb4_0900_bin", "utf8mb4_nopad_bin")

# Character sets whose bytes are UTF-8 (ascii is a subset of it), so that their byte order is code point order
_UTF8_CHARSETS = {"utf8mb4", "utf8mb3", "utf8", "ascii"}


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

    # The server's NO PAD binary collation (one of NOPAD_BINARY_COLLATIONS), or None if it has none.
    # Set per connection by MySQL.query_table_schema(), and used by collate_key().
    nopad_binary_collation: Optional[str] = None

    def collate_key(self, value: str, coltype: ColType) -> str:
        """Order string keys by code point, like Python and the other databases do.

        MySQL compares strings according to the column's collation. Case-insensitive and accent-insensitive
        collations (the defaults: utf8mb4_0900_ai_ci, utf8mb4_general_ci, latin1_swedish_ci, ...) put
        'a' before 'B', while Python, Snowflake, Postgres (C locale), BigQuery, etc. put 'B' before 'a'.
        Bisecting an alphanumeric key range with such a collation assigns rows to different segments on
        the two sides of the diff, which reports rows as missing or extra when they are not.

        Columns that already use a binary collation (``*_bin``, ``binary``) are left alone, so their index
        stays usable for range scans. The others are compared in a NO PAD binary collation, converted to
        utf8mb4 first unless they already are: ``key collate utf8mb4_0900_bin`` (MySQL 8.0.17+) or
        ``key collate utf8mb4_nopad_bin`` (MariaDB). Servers that have neither (MySQL 5.7, 8.0 before
        8.0.17) compare the UTF-8 bytes: ``cast(key as binary)``. All of these order exactly like Python,
        and none changes the table: they are expressions in namidiff's queries. The database cannot use the
        column's index for them though, so every segment query scans the table, unless the table has an
        index on the same expression (see docs/supported-databases.md).

        The conversion to utf8mb4 matters beyond ordering: the min/max keys come back through
        ``cast(... as char)``, and the bytes of e.g. a latin1 'é' are not valid UTF-8.

        UUID keys are not affected: namidiff compares them numerically, and the case-insensitive
        collations order hex digits the same way.
        """
        if not isinstance(coltype, String_Alphanum):
            return value

        collation = coltype.collation
        if collation is not None and is_binary_collation(collation):
            return value

        charset = collation.split("_", 1)[0] if collation else None
        if self.nopad_binary_collation is not None:
            utf8mb4 = value if charset == "utf8mb4" else f"convert({value} using utf8mb4)"
            expr = f"{utf8mb4} collate {self.nopad_binary_collation}"
        else:
            utf8 = value if charset in _UTF8_CHARSETS else f"convert({value} using utf8mb4)"
            expr = f"cast({utf8} as binary)"
        self._warn_key_not_sargable(value, collation, expr)
        return expr

    def _warn_key_not_sargable(self, column: str, collation: Optional[str], expr: str) -> None:
        "Warn once per key column (and connection) that the column's index cannot be used for its key ranges."
        warned = self.__dict__.setdefault("_collated_keys_warned", set())
        if (column, collation) in warned:
            return
        warned.add((column, collation))

        reason = (
            f"has collation '{collation}', which does not order strings by code point"
            if collation
            else "has an unknown collation"
        )
        index = f"ALTER TABLE <table> ADD INDEX (({expr}))"
        if self.nopad_binary_collation == "utf8mb4_0900_bin":
            # Only MySQL has this collation, and it supports functional indexes since 8.0.13
            hint = f"If the diff is too slow, add a functional index: {index}"
        elif self.nopad_binary_collation is None:
            hint = (
                "If the diff is too slow and the server is MySQL 8.0.13+, add a functional index "
                f"(possible for keys up to 512 bytes, e.g. utf8mb4 VARCHAR(128)): {index}"
            )
        else:
            hint = "MariaDB cannot index this expression."
        logger.warning(
            f"MySQL key column {column} {reason}. namidiff compares its key ranges as {expr}, "
            f"which cannot use the column's index, so every segment query scans the table. {hint}"
        )


class MySQL(ThreadedDatabase):
    dialect = Dialect()
    # String keys work with any collation: Dialect.collate_key() orders them by code point.
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

        self._nopad_detection_lock = threading.Lock()
        self._nopad_detected = False

    def query_table_schema(self, path: DbPath):
        self._detect_nopad_binary_collation()
        return super().query_table_schema(path)

    def _detect_nopad_binary_collation(self) -> None:
        """Find the server's NO PAD binary collation, for Dialect.collate_key(). Once per connection.

        Like set_timestamp_precision(), creates a per-instance dialect copy, so that connections
        to other servers are not affected.
        """
        with self._nopad_detection_lock:
            if self._nopad_detected:
                return
            names = ", ".join(f"'{c}'" for c in NOPAD_BINARY_COLLATIONS)
            available = set(
                self.query(
                    f"SELECT collation_name FROM information_schema.collations WHERE collation_name IN ({names})",
                    List[str],
                )
            )
            collation = next((c for c in NOPAD_BINARY_COLLATIONS if c in available), None)
            if collation is not None and collation != self.dialect.nopad_binary_collation:
                dialect_cls = type(self.dialect)

                class _DialectWithNoPadBinaryCollation(dialect_cls):
                    nopad_binary_collation = collation

                _DialectWithNoPadBinaryCollation.__name__ = dialect_cls.__name__
                self.dialect = _DialectWithNoPadBinaryCollation()
            self._nopad_detected = True

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
