"""Quality processing layers.

The public HTTP handlers remain in ``quality_checker`` and ``quality_fixer``.
This package owns their strategy-aware dispatch decisions so content rules never
need to infer a Language Learning pack from a generated JSON file.
"""
