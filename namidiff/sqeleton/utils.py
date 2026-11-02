from typing import (
    Iterable,
    Iterator,
    MutableMapping,
    Union,
    Any,
    Sequence,
    Dict,
    Hashable,
    TypeVar,
    Generator,
    List,
)
from abc import ABC, abstractmethod
from weakref import ref
import math
import string
import re
from uuid import UUID
from urllib.parse import urlparse

# -- Common --

try:
    from typing import Self
except ImportError:
    Self = Any


class WeakCache:
    def __init__(self):
        self._cache = {}

    def _hashable_key(self, k: Union[dict, Hashable]) -> Hashable:
        if isinstance(k, dict):
            return tuple(k.items())
        return k

    def add(self, key: Union[dict, Hashable], value: Any):
        key = self._hashable_key(key)
        self._cache[key] = ref(value)

    def get(self, key: Union[dict, Hashable]) -> Any:
        key = self._hashable_key(key)

        value = self._cache[key]()
        if value is None:
            del self._cache[key]
            raise KeyError(f"Key {key} not found, or no longer a valid reference")

        return value


def join_iter(joiner: Any, iterable: Iterable) -> Iterable:
    it = iter(iterable)
    try:
        yield next(it)
    except StopIteration:
        return
    for i in it:
        yield joiner
        yield i


def safezip(*args):
    "zip but makes sure all sequences are the same length"
    lens = list(map(len, args))
    if len(set(lens)) != 1:
        raise ValueError(f"Mismatching lengths in arguments to safezip: {lens}")
    return zip(*args)


def is_uuid(u):
    try:
        UUID(u)
    except ValueError:
        return False
    return True


def match_regexps(regexps: Dict[str, Any], s: str) -> Generator[tuple, None, None]:
    for regexp, v in regexps.items():
        m = re.match(regexp + "$", s)
        if m:
            yield m, v


# -- Schema --

V = TypeVar("V")


class CaseAwareMapping(MutableMapping[str, V]):
    @abstractmethod
    def get_key(self, key: str) -> str:
        ...

    @abstractmethod
    def __init__(self, initial):
        ...

    def new(self, initial=()):
        return type(self)(initial)


class CaseInsensitiveDict(CaseAwareMapping[V]):
    def __init__(self, initial):
        self._dict = {k.lower(): (k, v) for k, v in dict(initial).items()}

    def __getitem__(self, key: str) -> V:
        return self._dict[key.lower()][1]

    def __iter__(self) -> Iterator[str]:
        return iter(self._dict)

    def __len__(self) -> int:
        return len(self._dict)

    def __setitem__(self, key: str, value):
        k = key.lower()
        if k in self._dict:
            key = self._dict[k][0]
        self._dict[k] = key, value

    def __delitem__(self, key: str):
        del self._dict[key.lower()]

    def get_key(self, key: str) -> str:
        return self._dict[key.lower()][0]

    def __repr__(self) -> str:
        return repr(dict(self.items()))


class CaseSensitiveDict(dict, CaseAwareMapping):
    def get_key(self, key) -> str:
        self[key]  # Throw KeyError if key doesn't exist
        return key

    def as_insensitive(self):
        return CaseInsensitiveDict(self)


# -- Alphanumerics --

alphanums = " -." + string.digits + string.ascii_uppercase + "_" + string.ascii_lowercase


class ArithString(ABC):
    @classmethod
    def new(cls, *args, **kw):
        return cls(*args, **kw)

    def range(self, other: "ArithString", count: int):
        assert isinstance(other, ArithString)
        checkpoints = split_space(self.int, other.int, count)
        return [self.new(int=i) for i in checkpoints]

    # @property
    # @abstractmethod
    # def int(self):
    #     ...


class ArithUUID(UUID, ArithString):
    "A UUID that supports basic arithmetic (add, sub)"

    def __int__(self):
        return self.int

    def __add__(self, other: int):
        if isinstance(other, int):
            return self.new(int=self.int + other)
        return NotImplemented

    def __sub__(self, other: Union[UUID, int]):
        if isinstance(other, int):
            return self.new(int=self.int - other)
        elif isinstance(other, UUID):
            return self.int - other.int
        return NotImplemented


def numberToAlphanum(num: int, base: str = alphanums) -> str:
    digits = []
    while num > 0:
        num, remainder = divmod(num, len(base))
        digits.append(remainder)
    return "".join(base[i] for i in digits[::-1])


def alphanumToNumber(alphanum: str, base: str = alphanums) -> int:
    num = 0
    for c in alphanum:
        num = num * len(base) + base.index(c)
    return num


# Positional encoding of alphanum strings that preserves Python string order.
# Each character is a digit 1..len(alphanums), and digit 0 marks "end of string",
# so that 'M' < 'M ' maps to [M, 0] < [M, 1]. (Plain base-66 with space as 0 would map both to the same number.)
_ALPHANUM_ORDER_BASE = len(alphanums) + 1


def _alphanum_to_ordered_number(s: str, length: int) -> int:
    num = 0
    for i in range(length):
        num = num * _ALPHANUM_ORDER_BASE + (alphanums.index(s[i]) + 1 if i < len(s) else 0)
    return num


def _ordered_number_to_alphanum(num: int, length: int) -> str:
    "Inverse of _alphanum_to_ordered_number. Monotonic (non-strictly) for any number of `length` digits."
    digits = []
    for _ in range(length):
        num, d = divmod(num, _ALPHANUM_ORDER_BASE)
        digits.append(d)
    chars = []
    for d in reversed(digits):
        if d == 0:  # End of string; anything after it can only make a longer string, which would break order
            break
        chars.append(alphanums[d - 1])
    return "".join(chars)


def alphanums_to_numbers(s1: str, s2: str):
    "Map two alphanum strings to integers whose order and difference agree with Python string order"
    length = max(len(s1), len(s2))
    return _alphanum_to_ordered_number(s1, length), _alphanum_to_ordered_number(s2, length)


class ArithAlphanumeric(ArithString):
    def __init__(self, s: str, max_len=None):
        if s is None:
            raise ValueError("Alphanum string cannot be None")
        if max_len and len(s) > max_len:
            raise ValueError(f"Length of alphanum value '{str}' is longer than the expected {max_len}")

        for ch in s:
            if ch not in alphanums:
                raise ValueError(f"Unexpected character {ch} in alphanum string")

        self._str = s
        self._max_len = max_len

    def __str__(self):
        s = self._str
        if self._max_len:
            s = s.rjust(self._max_len, alphanums[0])
        return s

    def __len__(self):
        return len(self._str)

    def __repr__(self):
        return f'alphanum"{self._str}"'

    def __add__(self, other: "Union[ArithAlphanumeric, int]") -> "ArithAlphanumeric":
        if isinstance(other, int):
            if other != 1:
                raise NotImplementedError("not implemented for arbitrary numbers")
            # Used as the exclusive upper bound after the max key, so it only has to sort after self.
            # Appending a character always does, even for 'zz' (base-66 increment wrapped to '-  ').
            # Not self + ' ': under PAD SPACE comparison (e.g. Postgres CHAR(n)) 'zz' == 'zz ',
            # so that bound would exclude the max key. '-' is the smallest non-space character.
            return type(self)(self._str + alphanums[1])

        return NotImplemented

    def range(self, other: "ArithAlphanumeric", count: int):
        assert isinstance(other, ArithAlphanumeric)
        length = max(len(self._str), len(other._str))
        n1, n2 = alphanums_to_numbers(self._str, other._str)
        split = split_space(n1, n2, count)
        # Decoding is monotonic but not strictly so (end-of-string digits truncate), so drop duplicates
        # and points that collapsed onto the bounds.
        points = []
        for n in split:
            s = _ordered_number_to_alphanum(n, length)
            if self._str < s < other._str and (not points or points[-1] < s):
                points.append(s)
        return [self.new(s) for s in points]

    def __sub__(self, other: "Union[ArithAlphanumeric, int]") -> float:
        if isinstance(other, ArithAlphanumeric):
            n1, n2 = alphanums_to_numbers(self._str, other._str)
            return n1 - n2

        return NotImplemented

    def __ge__(self, other):
        if not isinstance(other, type(self)):
            return NotImplemented
        return self._str >= other._str

    def __lt__(self, other):
        if not isinstance(other, type(self)):
            return NotImplemented
        return self._str < other._str

    def __eq__(self, other):
        if not isinstance(other, type(self)):
            return NotImplemented
        return self._str == other._str

    def new(self, *args, **kw):
        return type(self)(*args, **kw, max_len=self._max_len)


def number_to_human(n):
    millnames = ["", "k", "m", "b"]
    n = float(n)
    millidx = max(
        0,
        min(len(millnames) - 1, int(math.floor(0 if n == 0 else math.log10(abs(n)) / 3))),
    )

    return "{:.0f}{}".format(n / 10 ** (3 * millidx), millnames[millidx])


def split_space(start, end, count) -> list:
    size = end - start
    if not (isinstance(start, int) and isinstance(end, int)):
        # Fractional keys (Decimal/float): evenly spaced points; range() only takes ints
        step = size / (count + 1)
        return [start + step * i for i in range(1, count + 1)]
    assert count <= size, (count, size)
    return list(range(start, end, (size + 1) // (count + 1)))[1 : count + 1]


def remove_passwords_in_dict(d: dict, replace_with: str = "***"):
    for k, v in d.items():
        if k == "password":
            d[k] = replace_with
        elif isinstance(v, dict):
            remove_passwords_in_dict(v, replace_with)
        elif k.startswith("database"):
            d[k] = remove_password_from_url(v, replace_with)


def _join_if_any(sym, args):
    args = list(args)
    if not args:
        return ""
    return sym.join(str(a) for a in args if a)


def remove_password_from_url(url: str, replace_with: str = "***") -> str:
    parsed = urlparse(url)
    account = parsed.username or ""
    if parsed.password:
        account += ":" + replace_with
    host = _join_if_any(":", filter(None, [parsed.hostname, parsed.port]))
    netloc = _join_if_any("@", filter(None, [account, host]))
    replaced = parsed._replace(netloc=netloc)
    return replaced.geturl()


def match_like(pattern: str, strs: Sequence[str]) -> Iterable[str]:
    reo = re.compile(pattern.replace("%", ".*").replace("?", ".") + "$")
    for s in strs:
        if reo.match(s):
            yield s


class UnknownMeta(type):
    def __instancecheck__(self, instance):
        return instance is Unknown

    def __repr__(self):
        return "Unknown"


class Unknown(metaclass=UnknownMeta):
    def __nonzero__(self):
        raise TypeError()

    def __new__(cls, *args, **kwargs):
        raise RuntimeError("Unknown is a singleton")
