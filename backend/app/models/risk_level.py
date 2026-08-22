from enum import Enum


class RiskLevel(str, Enum):
    """Mission and link risk classification."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
