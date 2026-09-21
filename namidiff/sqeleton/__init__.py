"""
namidiff.sqeleton - query builder and database abstraction layer.

This is a vendored fork of Sqeleton (https://github.com/erezsh/sqeleton) by
Erez Shinan, MIT licensed, bundled inside namidiff so that it can be modified
together with the diffing code and does not clash with the standalone
``sqeleton`` distribution on PyPI. See the top-level LICENSE for attribution.
"""

from .databases import connect
from .queries import table, this, SKIP, code, commit

# Version of upstream Sqeleton this fork was taken from.
__version__ = "0.1.8"
