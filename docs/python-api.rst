Python API Reference
====================

.. py:module:: namidiff

.. autofunction:: connect

.. autofunction:: connect_to_table

.. autofunction:: diff_tables

.. autoclass:: HashDiffer
    :members: __init__, diff_tables

.. autoclass:: JoinDiffer
    :members: __init__, diff_tables

.. autoclass:: TableSegment
    :members: __init__, get_values, choose_checkpoints, segment_by_checkpoints, count, count_and_checksum, is_bounded, new, with_schema

.. autoclass:: DiffResultWrapper
    :members: __iter__, close, get_stats_dict, get_stats_string

.. autoclass:: namidiff.databases.database_types.AbstractDatabase
    :members:

.. autoclass:: namidiff.databases.database_types.AbstractDialect
    :members:

.. autodata:: DbKey
.. autodata:: DbTime
.. autodata:: DbPath
.. autoenum:: Algorithm
