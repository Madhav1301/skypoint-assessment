"""Field parsers: pure functions, one per field family.

Every parser returns (cleaned_value, reason_code): exactly one of the two is
None. A null value always carries a reason; a parsed value never does.
Per-system conventions (date order, amount unit, time zone) are passed in
explicitly — parsers read no global state, which keeps them unit-testable.
"""
