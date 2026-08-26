#!/usr/bin/env python3
"""ASTERIA-7 Demo Scenario Generator.

Generates: data/scenarios/asteria7_thermal_priority_contact_v1.json
Run from project root:
    python tools/generate_asteria7_demo.py
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
_OUT_PATH = _PROJECT_ROOT / "data" / "scenarios" / "asteria7_thermal_priority_contact_v1.json"

# ---------------------------------------------------------------------------
# Fixed seed — deterministic generation
# ---------------------------------------------------------------------------
_SEED = 20240923
rng = np.random.default_rng(_SEED)
py_rng = random.Random(_SEED)

# ---------------------------------------------------------------------------
# Scenario-level constants
# ---------------------------------------------------------------------------
SCENARIO_ID = "asteria7_thermal_priority_contact_v1"
DISTANCE_KM = 182273814.464
CONTACT_WINDOW_S = 272.0

# ---------------------------------------------------------------------------
# Anchor products (exact specs)
# ---------------------------------------------------------------------------
ANCHORS = [
    {
        "product_id": "TEL-THERM-HR-042",
        "product_type": "telemetry",
        "subsystem": "thermal",
        "size_bits": 176_000_000,   # 22,000,000 bytes * 8
        "criticality": 0.99,
        "mission_relevance": 1.00,
        "scientific_value": 0.88,
        "deadline_s": 90.0,
        "age_s": 18.0,
        "anomaly_id": "ANOM-THERM-017",
        "delivery_requirement": "required",
        "retry_cost": 0.75,
        "related_ids": ["CAL-THERM-006", "DIAG-THERM-EVT-017", "TEL-PWR-CORR-031"],
        "description": (
            "High-rate thermal telemetry for the avionics bay captured during the active "
            "ANOM-THERM-017 anomaly window. The rolling buffer contains approximately "
            "18 seconds of recent high-fidelity temperature, flow-sensor, and "
            "cooling-loop data. This buffer cannot be reliably reconstructed after "
            "overwrite; downlinking now preserves the only copy of the thermal history "
            "immediately surrounding the anomaly onset for ground diagnosis."
        ),
    },
    {
        "product_id": "DIAG-THERM-EVT-017",
        "product_type": "diagnostic",
        "subsystem": "thermal",
        "size_bits": 92_000_000,    # 11,500,000 bytes * 8
        "criticality": 0.98,
        "mission_relevance": 0.99,
        "scientific_value": 0.89,
        "deadline_s": 128.0,
        "age_s": 42.0,
        "anomaly_id": "ANOM-THERM-017",
        "delivery_requirement": "required",
        "retry_cost": 0.72,
        "related_ids": ["FDIR-THERM-017", "CMD-THERM-571", "TEL-THERM-HR-042"],
        "description": (
            "Diagnostic event timeline log capturing the sequence of thermal readings, "
            "threshold crossings, and FDIR triggers surrounding the ANOM-THERM-017 "
            "anomaly onset approximately 42 seconds ago. Provides the event chronology "
            "needed to reconstruct cause and timeline for ground-based diagnosis."
        ),
    },
    {
        "product_id": "TEL-PWR-CORR-031",
        "product_type": "telemetry",
        "subsystem": "power",
        "size_bits": 76_000_000,    # 9,500,000 bytes * 8
        "criticality": 0.94,
        "mission_relevance": 0.96,
        "scientific_value": 0.82,
        "deadline_s": 160.0,
        "age_s": 55.0,
        "anomaly_id": "ANOM-THERM-017",
        "delivery_requirement": "required",
        "retry_cost": 0.68,
        "related_ids": ["TEL-THERM-HR-042"],
        "description": (
            "Power subsystem telemetry captured for cross-correlation with the active "
            "thermal anomaly. Includes bus voltage, load current, and heater duty-cycle "
            "records. Correlating thermal rise with power loading is required to rule "
            "out a spacecraft-wide power transient as the anomaly root cause."
        ),
    },
    {
        "product_id": "DIAG-COM-LINK-088",
        "product_type": "diagnostic",
        "subsystem": "communications",
        "size_bits": 96_000_000,    # 12,000,000 bytes * 8
        "criticality": 0.90,
        "mission_relevance": 0.93,
        "scientific_value": 0.78,
        "deadline_s": 205.0,
        "age_s": 70.0,
        "anomaly_id": None,
        "delivery_requirement": "required",
        "retry_cost": 0.62,
        "related_ids": ["NAV-ATT-214"],
        "description": (
            "Communication link diagnostics showing the degrading SNR trend and link "
            "margin history leading into this contact window. Documents the ongoing "
            "decline in link quality, supporting link-budget analysis and scheduling "
            "decisions for future passes."
        ),
    },
    {
        "product_id": "NAV-ATT-214",
        "product_type": "navigation",
        "subsystem": "attitude_control",
        "size_bits": 64_000_000,    # 8,000,000 bytes * 8
        "criticality": 0.88,
        "mission_relevance": 0.92,
        "scientific_value": 0.76,
        "deadline_s": 230.0,
        "age_s": 82.0,
        "anomaly_id": None,
        "delivery_requirement": "required",
        "retry_cost": 0.58,
        "related_ids": ["DIAG-COM-LINK-088"],
        "description": (
            "Attitude and antenna-pointing state record from the current contact arc. "
            "Documents spacecraft orientation and articulation history relevant to "
            "explaining the observed link degradation and for validating that pointing "
            "is not the cause of the reduced SNR."
        ),
    },
    {
        "product_id": "FDIR-THERM-017",
        "product_type": "diagnostic",
        "subsystem": "flight_computer",
        "size_bits": 25_600_000,    # 3,200,000 bytes * 8
        "criticality": 0.97,
        "mission_relevance": 0.99,
        "scientific_value": 0.85,
        "deadline_s": 240.0,
        "age_s": 24.0,
        "anomaly_id": "ANOM-THERM-017",
        "delivery_requirement": "required",
        "retry_cost": 0.70,
        "related_ids": ["DIAG-THERM-EVT-017", "CMD-THERM-571"],
        "description": (
            "Fault Detection, Isolation, and Recovery (FDIR) decision log for the "
            "ANOM-THERM-017 thermal anomaly. Records which autonomous responses were "
            "triggered, which thresholds were crossed, and the current FDIR state "
            "machine status. Essential context for understanding what the spacecraft "
            "has already done autonomously in response to the anomaly."
        ),
    },
    {
        "product_id": "CMD-THERM-571",
        "product_type": "command_ack",
        "subsystem": "thermal",
        "size_bits": 18_400_000,    # 2,300,000 bytes * 8
        "criticality": 0.96,
        "mission_relevance": 0.98,
        "scientific_value": 0.82,
        "deadline_s": 252.0,
        "age_s": 30.0,
        "anomaly_id": "ANOM-THERM-017",
        "delivery_requirement": "required",
        "retry_cost": 0.70,
        "related_ids": ["FDIR-THERM-017", "DIAG-THERM-EVT-017"],
        "description": (
            "Thermal-control command acknowledgement history showing which thermal "
            "management commands were uplinked and acknowledged during the ANOM-THERM-017 "
            "anomaly window. Provides the ground-commanded action record needed to "
            "reconstruct the full intervention timeline."
        ),
    },
    {
        "product_id": "CAL-THERM-006",
        "product_type": "diagnostic",
        "subsystem": "thermal",
        "size_bits": 112_640_000,   # 14,080,000 bytes * 8
        "criticality": 0.92,
        "mission_relevance": 0.95,
        "scientific_value": 0.88,
        "deadline_s": 272.0,
        "age_s": 110.0,
        "anomaly_id": "ANOM-THERM-017",
        "delivery_requirement": "required",
        "retry_cost": 0.65,
        "related_ids": ["TEL-THERM-HR-042", "DIAG-THERM-EVT-017"],
        "description": (
            "Thermal sensor calibration metadata and recent bias-correction context "
            "for the avionics bay sensors. Contains sensor offsets, calibration "
            "coefficients, and drift history needed to correctly interpret the "
            "ANOM-THERM-017 temperature readings and assess whether observed values "
            "reflect genuine hardware state or sensor-calibration artefacts."
        ),
    },
]

# ---------------------------------------------------------------------------
# Family distribution (EXACT totals required)
# ---------------------------------------------------------------------------
# Anchor bytes by family:
# thermal telemetry: TEL-THERM-HR-042 = 22,000,000 bytes
# thermal diagnostic: DIAG-THERM-EVT-017 = 11,500,000 + CAL-THERM-006 = 14,080,000 + FDIR-THERM-017 = 3,200,000 = 28,780,000
# power telemetry: TEL-PWR-CORR-031 = 9,500,000
# comm diagnostic: DIAG-COM-LINK-088 = 12,000,000
# nav: NAV-ATT-214 = 8,000,000
# command_ack: CMD-THERM-571 = 2,300,000

# Family specs: (family_key, product_type, subsystem_hint, count, total_bytes, anchor_bytes, anchor_count)
FAMILY_SPECS = [
    # family_key,           product_type,          count, total_bytes,   anchor_bytes_used,  anchor_count
    ("science_imagery",     "science",              60,    1_200_000_000, 0,                  0),
    ("experiment_results",  "experiment",           90,    720_000_000,   0,                  0),
    ("engineering_snap",    "engineering",          40,    246_400_000,   0,                  0),
    ("routine_telemetry",   "telemetry",            420,   210_000_000,   22_000_000,         1),  # TEL-THERM-HR-042
    ("subsystem_diag",      "diagnostic",           180,   216_000_000,   (28_780_000
                                                                           + 12_000_000),     4),  # DIAG-THERM-EVT-017,CAL-THERM-006,FDIR-THERM-017,DIAG-COM-LINK-088
    ("hrt_thermal",         "telemetry",            90,    54_000_000,    0,                  0),  # High-rate thermal - separate from routine_telemetry anchors
    ("power_telemetry",     "telemetry",            100,   40_000_000,    9_500_000,          1),  # TEL-PWR-CORR-031
    ("nav_records",         "navigation",           160,   40_000_000,    8_000_000,          1),  # NAV-ATT-214
    ("fault_event_logs",    "diagnostic",           64,    9_600_000,     0,                  0),
    ("cmd_ack_bundles",     "command_ack",          80,    4_000_000,     2_300_000,          1),  # CMD-THERM-571
]
# Total products = 60+90+40+420+180+90+100+160+64+80 = 1284 ✓
# Total bytes = 1.2B+720M+246.4M+210M+216M+54M+40M+40M+9.6M+4M = 2,740,000,000 ✓


def _generate_sizes_for_family(total_bytes: int, count: int, rng: np.random.Generator) -> list[int]:
    """Generate 'count' sizes summing to exactly 'total_bytes' with ±15% variation."""
    if count == 0:
        return []
    mean = total_bytes / count
    # Generate random proportions, then scale to total_bytes exactly
    raw = rng.uniform(0.85, 1.15, size=count)
    scaled = raw / raw.sum() * total_bytes
    sizes = scaled.astype(int).tolist()
    # Fix rounding so sum is exact
    diff = total_bytes - sum(sizes)
    sizes[0] += diff
    # Convert bytes to bits
    return [max(8, s) * 8 for s in sizes]  # at least 1 byte per product, in bits


def _bits_to_bytes(bits: int) -> int:
    return bits // 8


# ---------------------------------------------------------------------------
# Subsystem pools for realistic descriptions
# ---------------------------------------------------------------------------
_SUBSYSTEMS = {
    "science_imagery": "payload",
    "experiment_results": "payload",
    "engineering_snap": "flight_computer",
    "routine_telemetry": "thermal",
    "subsystem_diag": "thermal",
    "hrt_thermal": "thermal",
    "power_telemetry": "power",
    "nav_records": "navigation",
    "fault_event_logs": "flight_computer",
    "cmd_ack_bundles": "communications",
}

_SUBSYSTEM_DESCRIPTIONS = {
    "science_imagery": [
        "Wide-field survey image captured by the primary payload imager during nominal observation sequence.",
        "Narrowband spectral image from payload sensor array, part of the scheduled science observation campaign.",
        "High-resolution context image for surface mapping mission objective. Captured during inertial hold.",
        "Calibration-bracketed science image from the aft optical bench. Includes pre- and post-flat-field frames.",
        "Multispectral imaging data from the secondary payload, targeting the designated survey region.",
    ],
    "experiment_results": [
        "Processed results from the active science experiment sequence. Includes calibrated sensor outputs and statistical summaries.",
        "Experiment outcome data from the most recent observation run. Instrument performed nominally.",
        "Science data product from scheduled experiment block. Contains raw and reduced measurement arrays.",
        "Results from payload experiment executed during the current orbital segment. Quality flags nominal.",
        "Aggregated science telemetry from the ongoing experiment. Timestamped measurement set with housekeeping overlay.",
    ],
    "engineering_snap": [
        "Engineering snapshot of the onboard compute environment captured during nominal operations.",
        "Full-system state snapshot for ground review. Includes processor utilisation, memory map, and bus arbitration log.",
        "Engineering health snapshot recording register-level state of flight software critical modules.",
        "Periodic engineering state capture covering memory integrity, watchdog state, and task scheduler metrics.",
        "Avionics engineering snapshot. Includes I2C/SPI bus health, register file dump, and thermal-compensation coefficients.",
    ],
    "routine_telemetry": [
        "Routine housekeeping telemetry covering power, thermal, and processor health over the current operational interval.",
        "Standard telemetry downlink covering all subsystem health metrics for the scheduled reporting period.",
        "Recurring telemetry package with voltage, temperature, and attitude sensor readings.",
        "Nominal health telemetry from the most recent reporting window. No anomalies flagged.",
        "Standard operational telemetry bundle. Covers communications, thermal, and guidance subsystems.",
    ],
    "subsystem_diag": [
        "Diagnostic data package capturing subsystem-level performance metrics and fault counters.",
        "Subsystem diagnostic record covering recent operational history and latent health indicators.",
        "Health diagnostic for the specified subsystem, including BITE output and threshold exceedance history.",
        "Onboard diagnostic capture following scheduled self-test. All results within expected ranges.",
        "Diagnostic log for anomaly investigation support. Contains decision tree traces and sensor residuals.",
    ],
    "hrt_thermal": [
        "High-rate thermal telemetry from the avionics bay thermal sensor network.",
        "Continuous thermal monitoring data at elevated sample rate from the thermal-management subsystem.",
        "High-resolution thermal time-series from the avionics enclosure sensor ring.",
        "High-rate sampling record from the cooling-loop flow sensors and bay temperature probes.",
        "Thermal engineering data captured at high sample rate to support trend-analysis and anomaly monitoring.",
    ],
    "power_telemetry": [
        "Power subsystem telemetry covering bus voltage, battery state-of-charge, and load-current distribution.",
        "Electrical power system telemetry from the main power distribution unit for the current reporting interval.",
        "Power telemetry bundle including solar array current, battery depth-of-discharge, and heater activity.",
        "EPS operational data covering charge controller state, cell-balancing activity, and power margin.",
        "Battery and bus telemetry snapshot. Includes coulomb-counter data and cell-voltage imbalance metrics.",
    ],
    "nav_records": [
        "Navigation state record from the onboard navigation processor covering the current orbital segment.",
        "Attitude determination output including star-tracker fix, gyro integration residuals, and pointing error.",
        "Navigation telemetry from the inertial measurement unit and GPS receiver for the reporting epoch.",
        "Orbital position and velocity record from the onboard navigation computer. Uncertainty within specification.",
        "Navigation data product covering attitude quaternion history and manoeuvre execution accuracy.",
    ],
    "fault_event_logs": [
        "Fault log capturing all FDIR-triggered events and threshold exceedances for the reporting period.",
        "Event log recording anomaly detection flags, reset events, and autonomous safe-mode transitions.",
        "Fault and event log from the onboard FDIR system. Documents all non-nominal state changes.",
        "Latent-fault accumulation log for preventive maintenance analysis.",
        "Event-driven log containing timing, severity, and context for all flagged flight-software exceptions.",
    ],
    "cmd_ack_bundles": [
        "Command acknowledgement bundle confirming receipt and execution status of uplinked commands.",
        "Uplink command execution confirmation bundle for the most recent telecommand sequence.",
        "Command acknowledgement log from the onboard command decoder. All commands verified.",
        "Telecommand acknowledgement record documenting command sequence IDs, execution times, and status codes.",
        "Command verification bundle confirming all ground-uplinked commands were properly decoded and executed.",
    ],
}


def _pick_desc(family_key: str, py_rng: random.Random) -> str:
    return py_rng.choice(_SUBSYSTEM_DESCRIPTIONS[family_key])


# ---------------------------------------------------------------------------
# 15 supporting urgent/operational curated products (IDs 1-15)
# ---------------------------------------------------------------------------
# These ensure exactly 23 meet the urgent predicate (8 anchors + 15 supporting)
# Predicate: anomaly_id linked to active anomaly OR delivery_requirement=="required" OR deadline_s <= 272.0
# All 8 anchors meet it. We need exactly 15 more from the curated set.
SUPPORTING_PRODUCTS = [
    # 5 with anomaly link (also required, high crit)
    {
        "product_id": "TEL-THERM-BAY-101",
        "product_type": "telemetry", "subsystem": "thermal",
        "size_bits": 32_000_000, "criticality": 0.85, "mission_relevance": 0.88,
        "scientific_value": 0.70, "deadline_s": 200.0, "age_s": 90.0,
        "anomaly_id": "ANOM-THERM-017", "delivery_requirement": "required",
        "retry_cost": 0.55, "related_ids": ["TEL-THERM-HR-042"],
        "description": "Avionics bay area thermal telemetry providing spatial context for the active ANOM-THERM-017 anomaly. Complements the high-rate buffer with sensor locations across the bay.",
    },
    {
        "product_id": "TEL-THERM-COOL-102",
        "product_type": "telemetry", "subsystem": "thermal",
        "size_bits": 24_000_000, "criticality": 0.83, "mission_relevance": 0.86,
        "scientific_value": 0.68, "deadline_s": 210.0, "age_s": 100.0,
        "anomaly_id": "ANOM-THERM-017", "delivery_requirement": "required",
        "retry_cost": 0.52, "related_ids": ["TEL-THERM-HR-042"],
        "description": "Cooling-loop flow-rate and pressure telemetry for the avionics bay primary loop. Needed to characterise whether flow reduction is contributing to the ANOM-THERM-017 temperature rise.",
    },
    {
        "product_id": "HK-AVION-THERM-103",
        "product_type": "telemetry", "subsystem": "thermal",
        "size_bits": 16_000_000, "criticality": 0.80, "mission_relevance": 0.84,
        "scientific_value": 0.65, "deadline_s": 220.0, "age_s": 60.0,
        "anomaly_id": "ANOM-THERM-017", "delivery_requirement": "required",
        "retry_cost": 0.50, "related_ids": ["DIAG-THERM-EVT-017"],
        "description": "Avionics housekeeping thermal record correlating processor load with bay temperature. Supports ruling out processor thermal runaway as a factor in ANOM-THERM-017.",
    },
    {
        "product_id": "DIAG-THERM-TREND-104",
        "product_type": "diagnostic", "subsystem": "thermal",
        "size_bits": 20_000_000, "criticality": 0.82, "mission_relevance": 0.85,
        "scientific_value": 0.71, "deadline_s": 240.0, "age_s": 75.0,
        "anomaly_id": "ANOM-THERM-017", "delivery_requirement": "required",
        "retry_cost": 0.53, "related_ids": ["TEL-THERM-HR-042", "CAL-THERM-006"],
        "description": "Thermal trend analysis context captured by onboard diagnostics, including rate-of-change statistics and threshold-exceedance counters. Supplements FDIR logs for ground trending.",
    },
    {
        "product_id": "LOG-THERM-HIST-105",
        "product_type": "diagnostic", "subsystem": "thermal",
        "size_bits": 14_000_000, "criticality": 0.78, "mission_relevance": 0.82,
        "scientific_value": 0.67, "deadline_s": 260.0, "age_s": 130.0,
        "anomaly_id": "ANOM-THERM-017", "delivery_requirement": "required",
        "retry_cost": 0.48, "related_ids": ["DIAG-THERM-EVT-017"],
        "description": "Historical thermal log context from the 10-hour window preceding ANOM-THERM-017 onset. Used to establish baseline temperature profile and identify any precursor events.",
    },
    # 5 with required delivery_requirement (no anomaly, but required)
    {
        "product_id": "TEL-PWR-BUS-201",
        "product_type": "telemetry", "subsystem": "power",
        "size_bits": 28_000_000, "criticality": 0.75, "mission_relevance": 0.78,
        "scientific_value": 0.60, "deadline_s": 250.0, "age_s": 80.0,
        "anomaly_id": None, "delivery_requirement": "required",
        "retry_cost": 0.40, "related_ids": [],
        "description": "Primary power bus telemetry snapshot required for mission operations review. Includes voltage history and load-shedding event log.",
    },
    {
        "product_id": "NAV-ORBIT-202",
        "product_type": "navigation", "subsystem": "navigation",
        "size_bits": 22_000_000, "criticality": 0.72, "mission_relevance": 0.76,
        "scientific_value": 0.58, "deadline_s": 265.0, "age_s": 95.0,
        "anomaly_id": None, "delivery_requirement": "required",
        "retry_cost": 0.38, "related_ids": [],
        "description": "Orbital state vector record required for ground navigation update processing before the next manoeuvre window.",
    },
    {
        "product_id": "HK-SYS-HEALTH-203",
        "product_type": "telemetry", "subsystem": "flight_computer",
        "size_bits": 18_000_000, "criticality": 0.71, "mission_relevance": 0.74,
        "scientific_value": 0.55, "deadline_s": 268.0, "age_s": 40.0,
        "anomaly_id": None, "delivery_requirement": "required",
        "retry_cost": 0.36, "related_ids": [],
        "description": "Full system health snapshot required for mission status reporting. Covers all subsystem health flags and onboard time synchronisation state.",
    },
    {
        "product_id": "COM-LINK-STAT-204",
        "product_type": "diagnostic", "subsystem": "communications",
        "size_bits": 12_000_000, "criticality": 0.73, "mission_relevance": 0.77,
        "scientific_value": 0.59, "deadline_s": 270.0, "age_s": 50.0,
        "anomaly_id": None, "delivery_requirement": "required",
        "retry_cost": 0.39, "related_ids": ["DIAG-COM-LINK-088"],
        "description": "Communication subsystem link-statistics log required for pass-performance reporting and uplink planning for the next contact window.",
    },
    {
        "product_id": "CMD-SEQ-ACK-205",
        "product_type": "command_ack", "subsystem": "flight_computer",
        "size_bits": 8_000_000, "criticality": 0.70, "mission_relevance": 0.73,
        "scientific_value": 0.54, "deadline_s": 272.0, "age_s": 20.0,
        "anomaly_id": None, "delivery_requirement": "required",
        "retry_cost": 0.35, "related_ids": [],
        "description": "Command sequence acknowledgement bundle for the most recent uplinked command block. Required to confirm execution before the next uplink sequence.",
    },
    # 5 with deadline_s <= 272.0 AND criticality >= 0.70 (no anomaly, best_effort)
    {
        "product_id": "SCI-IMG-PRIO-301",
        "product_type": "science", "subsystem": "payload",
        "size_bits": 120_000_000, "criticality": 0.77, "mission_relevance": 0.80,
        "scientific_value": 0.92, "deadline_s": 120.0, "age_s": 200.0,
        "anomaly_id": None, "delivery_requirement": "best_effort",
        "retry_cost": 0.45, "related_ids": [],
        "description": "Priority science image from the scheduled observation target. Deadline reflects the perishable nature of the observing geometry; the target will exit the optimal viewing window.",
    },
    {
        "product_id": "SCI-IMG-PRIO-302",
        "product_type": "science", "subsystem": "payload",
        "size_bits": 110_000_000, "criticality": 0.76, "mission_relevance": 0.79,
        "scientific_value": 0.91, "deadline_s": 180.0, "age_s": 180.0,
        "anomaly_id": None, "delivery_requirement": "best_effort",
        "retry_cost": 0.44, "related_ids": [],
        "description": "Second priority science image from the observation sequence. Paired with SCI-IMG-PRIO-301 for stereo reconstruction; both needed for full science return.",
    },
    {
        "product_id": "EXP-RESULT-303",
        "product_type": "experiment", "subsystem": "payload",
        "size_bits": 80_000_000, "criticality": 0.74, "mission_relevance": 0.77,
        "scientific_value": 0.90, "deadline_s": 250.0, "age_s": 150.0,
        "anomaly_id": None, "delivery_requirement": "best_effort",
        "retry_cost": 0.42, "related_ids": [],
        "description": "Experiment results from the priority science objective scheduled for this pass. Ground processing pipeline has a time-bounded window before the next experiment execution.",
    },
    {
        "product_id": "SCI-SPEC-304",
        "product_type": "science", "subsystem": "payload",
        "size_bits": 60_000_000, "criticality": 0.73, "mission_relevance": 0.76,
        "scientific_value": 0.89, "deadline_s": 265.0, "age_s": 160.0,
        "anomaly_id": None, "delivery_requirement": "best_effort",
        "retry_cost": 0.41, "related_ids": [],
        "description": "Spectral science data from the primary sensor. Processing pipeline integration deadline within current contact window for same-orbit science product delivery.",
    },
    {
        "product_id": "EXP-CALIB-305",
        "product_type": "experiment", "subsystem": "payload",
        "size_bits": 50_000_000, "criticality": 0.70, "mission_relevance": 0.73,
        "scientific_value": 0.87, "deadline_s": 272.0, "age_s": 240.0,
        "anomaly_id": None, "delivery_requirement": "best_effort",
        "retry_cost": 0.40, "related_ids": [],
        "description": "Experiment calibration data product required for processing the co-timed science observation. Calibration context must arrive in the same downlink session to preserve science data quality.",
    },
]

# ---------------------------------------------------------------------------
# 27 high-value semantic distractors (scientific_value >= 0.70, deadlines OUTSIDE window)
# ---------------------------------------------------------------------------
DISTRACTOR_PRODUCTS = [
    {
        "product_id": f"SCI-DIST-{400 + i:03d}",
        "product_type": pt, "subsystem": ss,
        "size_bits": sb, "criticality": cr, "mission_relevance": mr,
        "scientific_value": sv, "deadline_s": dl, "age_s": ag,
        "anomaly_id": None, "delivery_requirement": "best_effort",
        "retry_cost": 0.0, "related_ids": [],
        "description": desc,
    }
    for i, (pt, ss, sb, cr, mr, sv, dl, ag, desc) in enumerate([
        ("science", "payload", 160_000_000, 0.65, 0.80, 0.95, 400.0, 300.0, "High-priority science image from the primary survey objective. Scientifically valuable but not time-critical within the current contact horizon."),
        ("experiment", "payload", 140_000_000, 0.62, 0.78, 0.94, 450.0, 320.0, "Experiment result set from the long-duration science run. High scientific return; deadline falls in the next pass window."),
        ("science", "payload", 130_000_000, 0.60, 0.77, 0.93, 500.0, 350.0, "Wide-field survey image with excellent spatial resolution. No time-critical constraint; deferred downlink acceptable."),
        ("experiment", "payload", 120_000_000, 0.58, 0.75, 0.92, 550.0, 400.0, "Spectrometric experiment output from the scheduled science campaign. Deadline is well outside the current contact window."),
        ("science", "payload", 115_000_000, 0.56, 0.74, 0.91, 600.0, 420.0, "Multispectral image set from the secondary payload sensor. High science value for the ongoing mapping campaign."),
        ("science", "payload", 110_000_000, 0.55, 0.72, 0.90, 650.0, 450.0, "Science image targeted at the high-priority survey region. Optimal for downlink in the subsequent pass."),
        ("experiment", "payload", 100_000_000, 0.54, 0.71, 0.90, 700.0, 480.0, "Long-duration experiment results, scientifically rich but not urgently time-bounded."),
        ("science", "payload", 95_000_000, 0.53, 0.70, 0.89, 750.0, 500.0, "High-resolution science image from the nominal observation sequence. Deferred downlink is acceptable."),
        ("experiment", "payload", 90_000_000, 0.52, 0.69, 0.88, 800.0, 520.0, "Experiment data from the active science campaign. No anomaly or deadline pressure within current window."),
        ("science", "payload", 85_000_000, 0.51, 0.68, 0.87, 850.0, 540.0, "Survey image from the secondary observation program. High scientific value, flexible downlink schedule."),
        ("science", "payload", 80_000_000, 0.50, 0.67, 0.86, 900.0, 560.0, "Calibrated science image from the primary payload. Scientifically attractive but fits next-pass downlink schedule."),
        ("experiment", "payload", 78_000_000, 0.49, 0.66, 0.86, 950.0, 580.0, "Processed experiment product with good science return. Deadline is in the next planned contact window."),
        ("science", "payload", 75_000_000, 0.48, 0.65, 0.85, 1000.0, 600.0, "Wide-field context image for mission science objectives. No immediate downlink urgency."),
        ("experiment", "payload", 72_000_000, 0.47, 0.64, 0.84, 1100.0, 620.0, "Science experiment output with high data quality. Flexible schedule; next-pass downlink preferred."),
        ("science", "payload", 70_000_000, 0.46, 0.63, 0.84, 1200.0, 640.0, "Narrowband imaging science data from the primary survey. No time-critical constraint."),
        ("experiment", "payload", 68_000_000, 0.45, 0.62, 0.83, 1300.0, 660.0, "Experiment result data from scheduled science run. High scientific interest; no current urgency."),
        ("science", "payload", 65_000_000, 0.44, 0.61, 0.82, 1400.0, 680.0, "Science image from the priority survey target. Best scheduled for the next downlink opportunity."),
        ("experiment", "payload", 62_000_000, 0.43, 0.60, 0.82, 1500.0, 700.0, "Spectrometric experiment data with strong science return. Not time-critical within the current pass."),
        ("science", "payload", 60_000_000, 0.42, 0.59, 0.81, 1600.0, 720.0, "Survey image from the science campaign archive. No pressing downlink deadline."),
        ("experiment", "payload", 58_000_000, 0.41, 0.58, 0.80, 1700.0, 740.0, "Experiment output from the most recent science observation block. Deferred downlink is acceptable."),
        ("science", "payload", 55_000_000, 0.40, 0.57, 0.80, 1800.0, 760.0, "High-quality science image for the mapping campaign. No operational urgency in this pass."),
        ("experiment", "payload", 52_000_000, 0.39, 0.56, 0.79, 1900.0, 780.0, "Science experiment data with good calibration quality. Best fit for next-pass downlink queue."),
        ("science", "payload", 50_000_000, 0.38, 0.55, 0.78, 2000.0, 800.0, "Wide-field science image from the secondary survey. Flexible downlink priority."),
        ("experiment", "payload", 48_000_000, 0.37, 0.54, 0.78, 2100.0, 820.0, "Processed experiment result. High scientific interest but no deadline within the current contact window."),
        ("science", "payload", 46_000_000, 0.36, 0.53, 0.77, 2200.0, 840.0, "Science imaging data from the nominal observation campaign. Deferred downlink consistent with mission plan."),
        ("experiment", "payload", 44_000_000, 0.35, 0.52, 0.76, 2300.0, 860.0, "Experiment data block from the long-duration science campaign. No urgency for the current pass."),
        ("science", "payload", 42_000_000, 0.34, 0.51, 0.75, 2400.0, 880.0, "Survey science image from the secondary target. No time pressure within the current contact window."),
    ])
]


# ---------------------------------------------------------------------------
# Background product descriptions (realistic, per-family)
# ---------------------------------------------------------------------------
_BG_DESCRIPTIONS = {
    "science_imagery":     "Routine science survey image from the ongoing observation campaign. No anomaly association; scheduled for downlink in a future pass.",
    "experiment_results":  "Science experiment result set from the current campaign. No anomaly or immediate time constraint; deferred downlink acceptable.",
    "engineering_snap":    "Periodic engineering state snapshot. Captured as part of routine health monitoring; no anomaly association.",
    "routine_telemetry":   "Routine subsystem telemetry from the standard housekeeping collection. All values within nominal bounds; deferred downlink acceptable.",
    "subsystem_diag":      "Subsystem diagnostic record from routine health monitoring. No anomaly linked; deferred downlink consistent with operational plan.",
    "hrt_thermal":         "High-rate thermal telemetry from the thermal monitoring subsystem. No anomaly association; routine monitoring data.",
    "power_telemetry":     "Power subsystem telemetry from the standard EPS monitoring cycle. All values within nominal bounds.",
    "nav_records":         "Navigation state record from the routine orbital determination cycle. Deferred downlink acceptable.",
    "fault_event_logs":    "Routine fault event log from the standard FDIR monitoring cycle. No active anomaly events recorded in this log.",
    "cmd_ack_bundles":     "Standard command acknowledgement bundle from the routine telecommand sequence. All commands verified; no anomaly-related commanding.",
}


def generate_background_products(
    family_specs: list,
    anchors: list[dict],
    supporting: list[dict],
    distractors: list[dict],
    rng_np: np.random.Generator,
    py_rng_local: random.Random,
) -> list[dict]:
    """Generate background products to fill exact family counts and byte totals."""

    anchor_ids = {a["product_id"] for a in anchors}
    curated_ids = {p["product_id"] for p in supporting + distractors}

    background = []

    # Build a map of how many bytes the curated products consume per family
    # We need to track which curated products belong to which family
    # Based on product type, we'll assign them
    # The anchors are placed within specific families based on their type:
    #   routine_telemetry: TEL-THERM-HR-042, TEL-PWR-CORR-031 is power_telemetry, NAV-ATT-214 is nav_records, CMD-THERM-571 is cmd_ack_bundles
    #   subsystem_diag: DIAG-THERM-EVT-017, CAL-THERM-006, FDIR-THERM-017, DIAG-COM-LINK-088

    # The key insight: we just need to generate exactly (count - curated_in_family) background
    # products with sizes summing to (total_bytes - curated_bytes_in_family) for each family.
    # We already have the curated counts and bytes baked into FAMILY_SPECS via anchor_bytes_used.

    # For simplicity: background products are the products NOT in the curated set (8+15+27=50 curated).
    # Total products = 1284, so 1234 background.
    # We distribute them across families per the spec counts minus curated counts per family.

    # Family assignment for curated:
    family_curated = {
        "science_imagery":    {"count": 0,  "bytes": 0},
        "experiment_results": {"count": 0,  "bytes": 0},
        "engineering_snap":   {"count": 0,  "bytes": 0},
        "routine_telemetry":  {"count": 0,  "bytes": 0},
        "subsystem_diag":     {"count": 0,  "bytes": 0},
        "hrt_thermal":        {"count": 0,  "bytes": 0},
        "power_telemetry":    {"count": 0,  "bytes": 0},
        "nav_records":        {"count": 0,  "bytes": 0},
        "fault_event_logs":   {"count": 0,  "bytes": 0},
        "cmd_ack_bundles":    {"count": 0,  "bytes": 0},
    }

    # Anchor family assignment:
    anchor_family = {
        "TEL-THERM-HR-042": "routine_telemetry",
        "DIAG-THERM-EVT-017": "subsystem_diag",
        "TEL-PWR-CORR-031": "power_telemetry",
        "DIAG-COM-LINK-088": "subsystem_diag",
        "NAV-ATT-214": "nav_records",
        "FDIR-THERM-017": "subsystem_diag",
        "CMD-THERM-571": "cmd_ack_bundles",
        "CAL-THERM-006": "subsystem_diag",
    }
    for a in anchors:
        fam = anchor_family[a["product_id"]]
        family_curated[fam]["count"] += 1
        family_curated[fam]["bytes"] += a["size_bits"] // 8

    # Supporting family assignment (by product type and subsystem):
    supporting_family = {
        "TEL-THERM-BAY-101": "routine_telemetry",
        "TEL-THERM-COOL-102": "routine_telemetry",
        "HK-AVION-THERM-103": "routine_telemetry",
        "DIAG-THERM-TREND-104": "subsystem_diag",
        "LOG-THERM-HIST-105": "subsystem_diag",
        "TEL-PWR-BUS-201": "power_telemetry",
        "NAV-ORBIT-202": "nav_records",
        "HK-SYS-HEALTH-203": "routine_telemetry",
        "COM-LINK-STAT-204": "subsystem_diag",
        "CMD-SEQ-ACK-205": "cmd_ack_bundles",
        "SCI-IMG-PRIO-301": "science_imagery",
        "SCI-IMG-PRIO-302": "science_imagery",
        "EXP-RESULT-303": "experiment_results",
        "SCI-SPEC-304": "science_imagery",
        "EXP-CALIB-305": "experiment_results",
    }
    for s in supporting:
        fam = supporting_family[s["product_id"]]
        family_curated[fam]["count"] += 1
        family_curated[fam]["bytes"] += s["size_bits"] // 8

    # Distractor family: all are "science" or "experiment" type
    for d in distractors:
        if d["product_type"] == "science":
            fam = "science_imagery"
        else:
            fam = "experiment_results"
        family_curated[fam]["count"] += 1
        family_curated[fam]["bytes"] += d["size_bits"] // 8

    # Now for each family, generate background products
    bg_idx = 0
    for spec in family_specs:
        family_key, product_type, total_count, total_bytes, _anchor_bytes, _anchor_count = spec
        curated_info = family_curated[family_key]
        bg_count = total_count - curated_info["count"]
        bg_bytes = total_bytes - curated_info["bytes"]

        if bg_count <= 0 or bg_bytes <= 0:
            continue

        sizes_bits = _generate_sizes_for_family(bg_bytes, bg_count, rng_np)
        subsystem = _SUBSYSTEMS[family_key]
        description = _BG_DESCRIPTIONS[family_key]

        for j, sb in enumerate(sizes_bits):
            pid = f"BG-{family_key.upper()[:8]}-{bg_idx + 1:04d}"
            bg_idx += 1
            # Background products: criticality < 0.70, deadline > 272s
            criticality = float(rng_np.uniform(0.10, 0.69))
            deadline = float(rng_np.uniform(300.0, 7200.0))
            age = float(rng_np.uniform(30.0, 3600.0))
            mission_relevance = float(rng_np.uniform(0.10, 0.65))
            scientific_value = float(rng_np.uniform(0.10, 0.65))

            background.append({
                "product_id": pid,
                "product_type": product_type,
                "subsystem": subsystem,
                "size_bits": sb,
                "criticality": round(criticality, 4),
                "mission_relevance": round(mission_relevance, 4),
                "scientific_value": round(scientific_value, 4),
                "deadline_s": round(deadline, 1),
                "age_s": round(age, 1),
                "anomaly_id": None,
                "experiment_id": None,
                "related_ids": [],
                "delivery_requirement": "best_effort",
                "retry_cost": 0.0,
                "description": description,
            })

    return background


def build_scenario() -> dict:
    """Build the complete ASTERIA-7 scenario as a dict."""

    # Combine curated (anchors + supporting + distractors)
    curated = ANCHORS + SUPPORTING_PRODUCTS + DISTRACTOR_PRODUCTS  # 50 products

    # Generate background products
    background = generate_background_products(
        FAMILY_SPECS, ANCHORS, SUPPORTING_PRODUCTS, DISTRACTOR_PRODUCTS, rng, py_rng
    )

    all_products = curated + background

    # Normalise all product fields
    normalised = []
    for p in all_products:
        normalised.append({
            "product_id": p["product_id"],
            "product_type": p["product_type"],
            "description": p.get("description", ""),
            "subsystem": p["subsystem"],
            "size_bits": int(p["size_bits"]),
            "criticality": round(float(p["criticality"]), 4),
            "mission_relevance": round(float(p["mission_relevance"]), 4),
            "scientific_value": round(float(p["scientific_value"]), 4),
            "deadline_s": round(float(p["deadline_s"]), 1),
            "age_s": round(float(p["age_s"]), 1),
            "anomaly_id": p.get("anomaly_id"),
            "experiment_id": p.get("experiment_id"),
            "related_ids": list(p.get("related_ids", [])),
            "delivery_requirement": p["delivery_requirement"],
            "retry_cost": round(float(p.get("retry_cost", 0.0)), 4),
        })

    scenario = {
        "scenario_id": SCENARIO_ID,
        "simulated": True,
        "distance_km": DISTANCE_KM,
        "link_inputs": {
            "timestamp": "2024-09-23T14:07:00+00:00",
            "snr_db": 2.8,
            "rssi_dbm": -103.6,
            "nominal_data_rate_bps": 2800000.0,
            "latency_s": 1.4,
            "link_stability": 0.68,
            "remaining_window_s": CONTACT_WINDOW_S,
        },
        "mission_state": {
            "mission_id": "GCSI-ASTERIA-7",
            "mission_phase": "pre_contact_anomaly_triage",
            "current_event": "Active avionics thermal anomaly; high-rate contact pending.",
            "event_time_remaining_s": 192.0,
            "comm_window_remaining_s": CONTACT_WINDOW_S,
            "risk_score": 0.72,
            "risk_level": "HIGH",
        },
        "packets": [],
        "anomalies": [
            {
                "anomaly_id": "ANOM-THERM-017",
                "subsystem": "thermal",
                "severity": 0.94,
                "detected_at_s": 664.0,
                "description": (
                    "The avionics bay crossed its upper nominal thermal operating envelope "
                    "approximately eleven minutes ago. Cooling-loop flow remains available, "
                    "but the most recent short-term trend has accelerated to approximately "
                    "+2.8 \u00b0C/min (recent short-term rate only; temperature has not been "
                    "rising at this rate continuously for the entire anomaly duration). Power "
                    "telemetry remains nominal while avionics temperature continues to rise, "
                    "reducing the likelihood that the event is caused by a spacecraft-wide "
                    "power transient. Propulsion remains nominal. The cause is unresolved. "
                    "Fresh thermal history, fault-control context, power correlation data, "
                    "and interpretation metadata are needed for ground diagnosis."
                ),
                "status": "active",
                "related_product_ids": [
                    "TEL-THERM-HR-042", "DIAG-THERM-EVT-017", "TEL-PWR-CORR-031",
                    "FDIR-THERM-017", "CMD-THERM-571", "CAL-THERM-006",
                ],
            }
        ],
        "data_products": normalised,
    }
    return scenario


def verify_scenario(scenario: dict) -> None:
    """Verify key invariants and print a report."""
    products = scenario["data_products"]
    total_products = len(products)
    total_bytes = sum(p["size_bits"] // 8 for p in products)
    total_bits = sum(p["size_bits"] for p in products)

    print(f"Total products : {total_products}")
    print(f"Total bytes    : {total_bytes:,}")
    print(f"Total bits     : {total_bits:,}")

    assert total_products == 1284, f"Expected 1284 products, got {total_products}"
    assert total_bytes == 2_740_000_000, f"Expected 2,740,000,000 bytes, got {total_bytes}"

    # Family counts
    from collections import Counter
    family_type_map = {
        "science_imagery": "science",
        "experiment_results": "experiment",
        "engineering_snap": "engineering",
        "routine_telemetry": "telemetry",
        "subsystem_diag": "diagnostic",
        "hrt_thermal": "telemetry_hrt",
        "power_telemetry": "telemetry_pwr",
        "nav_records": "navigation",
        "fault_event_logs": "fault_logs",
        "cmd_ack_bundles": "command_ack",
    }

    # Check all 8 anchor IDs are present
    product_ids = {p["product_id"] for p in products}
    anchor_ids = [a["product_id"] for a in ANCHORS]
    for aid in anchor_ids:
        assert aid in product_ids, f"Missing anchor: {aid}"

    print("\nAll 8 anchors present [OK]")
    print(f"\nUnique IDs: {len(product_ids)} (expected 1284)")
    assert len(product_ids) == 1284, "Duplicate product IDs detected"
    print("No duplicate IDs [OK]")
    print("\nAll invariants OK [OK]")


def main() -> None:
    _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    scenario = build_scenario()
    verify_scenario(scenario)
    with open(_OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(scenario, f, indent=2, ensure_ascii=False)
    print(f"\nWritten: {_OUT_PATH}")


if __name__ == "__main__":
    main()
