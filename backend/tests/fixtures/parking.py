from datetime import UTC, datetime, timedelta

EVALUATION_AT = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)

# The winning tier deliberately contains contradictory and unknown facts. Never
# identify a logical upstream rule using just its precedence tuple.
RULE_CASES = (
    {"scope": "lot", "rule_kind": "BASELINE", "authority_priority": 10, "large_heavy_allowed": True},
    {"scope": "zone", "rule_kind": "BASELINE", "authority_priority": 10, "large_heavy_allowed": True},
    {"scope": "zone", "rule_kind": "EXCEPTION", "authority_priority": 20, "large_heavy_allowed": True},
    {"scope": "zone", "rule_kind": "EXCEPTION", "authority_priority": 20, "large_heavy_allowed": False},
    {"scope": "zone", "rule_kind": "EXCEPTION", "authority_priority": 20, "large_heavy_allowed": None},
)

MIXED_REALTIME_CASES = (
    {
        "status": "AVAILABLE",
        "available_spaces": 3,
        "total_spaces": 10,
        "source_updated_at": EVALUATION_AT - timedelta(seconds=20),
        "fetched_at": EVALUATION_AT - timedelta(seconds=10),
    },
    {
        "status": "FULL",
        "available_spaces": 0,
        "total_spaces": 20,
        "source_updated_at": EVALUATION_AT - timedelta(hours=3),
        "fetched_at": EVALUATION_AT - timedelta(hours=2),
    },
    {"status": "UNKNOWN", "available_spaces": None, "total_spaces": None, "fetched_at": None},
    {"status": "AVAILABLE", "available_spaces": 1, "total_spaces": None, "fetched_at": EVALUATION_AT},
)

# Keep invalid input available to service/adapter tests. These cases must survive
# in raw evidence, but cannot become normalized trustworthy realtime facts.
INVALID_REALTIME_CASES = (
    {"status": "AVAILABLE", "available_spaces": -1, "total_spaces": 10},
    {"status": "UNKNOWN", "available_spaces": None, "total_spaces": -1},
    {"status": "AVAILABLE", "available_spaces": 11, "total_spaces": 10},
    {"status": "AVAILABLE", "available_spaces": 0, "total_spaces": 10},
    {"status": "AVAILABLE", "available_spaces": None, "total_spaces": 10},
    {"status": "FULL", "available_spaces": 1, "total_spaces": 10},
    {"status": "FULL", "available_spaces": None, "total_spaces": 10},
    {"status": "CLOSED", "available_spaces": 1, "total_spaces": 10},
    {"status": "CLOSED", "available_spaces": None, "total_spaces": 10},
)

# INTEGER is storage integrity, not strict input validation: PostgreSQL can round
# a fractional value before its CHECK runs. Normalizers must reject both float
# and bool counts before binding; preserve the original value in raw evidence.
NONINTEGER_REALTIME_CASES = (
    {"status": "AVAILABLE", "available_spaces": 1.5, "total_spaces": 10},
    {"status": "AVAILABLE", "available_spaces": True, "total_spaces": 10},
)
