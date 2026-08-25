"""Bridge utilities — convert Phase 2A domain models to legacy pipeline types.

``DataProduct`` and ``Packet`` share most scheduling-relevant fields (criticality,
mission_relevance, size_bits, deadline_s, retry_cost, delivery_requirement).  The
bridge function performs a lossless mapping so that any code path that currently
operates on ``list[Packet]`` can accept data from a v2 scenario without change.

Design constraints
------------------
- The bridge is a pure function with no side effects.
- No scheduling-specific fields (priority, rank, score) are added by the bridge.
- The resulting Packet is semantically identical to a hand-authored one: the
  evaluator, scheduler, and candidate generator see no difference.
- ``product_id`` maps to ``packet_id`` so all downstream logging and display
  references the original product identifier.
- Fields present on ``DataProduct`` but absent from ``Packet``
  (``scientific_value``, ``age_s``, ``anomaly_id``, ``experiment_id``,
  ``related_ids``, ``subsystem``) are silently dropped — they are available on
  the original ``DataProduct`` object for AI context enrichment at a higher layer.
"""

from __future__ import annotations

from .data_product import DataProduct
from .packet import Packet


def data_product_to_packet(dp: DataProduct) -> Packet:
    """Convert a :class:`DataProduct` to a :class:`Packet`.

    Uses the fields that are semantically identical across both models:

    ==================  ================  ===========
    DataProduct field   Packet field      Notes
    ==================  ================  ===========
    product_id          packet_id
    product_type        packet_type
    size_bits           size_bits
    criticality         criticality
    mission_relevance   mission_relevance
    deadline_s          deadline_s
    retry_cost          retry_cost
    delivery_requirement delivery_requirement
    ==================  ================  ===========

    Args:
        dp: The :class:`DataProduct` to convert.

    Returns:
        A :class:`Packet` suitable for use in the scheduling and evaluation pipeline.
    """
    return Packet(
        packet_id=dp.product_id,
        packet_type=dp.product_type,
        size_bits=dp.size_bits,
        criticality=dp.criticality,
        mission_relevance=dp.mission_relevance,
        deadline_s=dp.deadline_s,
        retry_cost=dp.retry_cost,
        delivery_requirement=dp.delivery_requirement,
    )


def data_products_to_packets(products: list[DataProduct]) -> list[Packet]:
    """Convert a list of :class:`DataProduct` objects to :class:`Packet` objects.

    Preserves list order.  Empty list returns empty list.

    Args:
        products: The list of :class:`DataProduct` objects to convert.

    Returns:
        A list of :class:`Packet` objects in the same order.
    """
    return [data_product_to_packet(dp) for dp in products]
