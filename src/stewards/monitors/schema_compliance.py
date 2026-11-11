"""The schema compliance snapshot -> one row per feed. No Streamlit, no I/O.

The page has no endpoint of its own. It joins two snapshots the API already serves:

- feed quality, which carries every assessed feed with its version, its missing required
  fields and its completeness for the key fields;
- custom properties, which lists only the feeds that use at least one non-standard
  property.

So quality decides which feeds are on the page, and custom properties adds detail to the
feeds it names. A feed missing from the custom-property list uses none, which is a real
zero. If the custom-property read failed, the whole list is unknown, which is not zero.

The rows reuse `quality.build_rows` and `schema_drift.build_row` rather than copying them,
so the labels and counts read the same on every page.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from stewards.api.models import SchemaComplianceSnapshot
from stewards.monitors import quality, schema_drift
from stewards.monitors.quality import QualityRow
from stewards.monitors.schema_drift import PropertyRow

#: The key fields the compliance view checks, in the order the page shows them. These are
#: the recommended fields the quality assessment measures. Optional fields are left out on
#: purpose: the task is adherence to required and recommended fields only.
KEY_FIELDS: tuple[str, ...] = (
    "location",
    "start_date",
    "end_date",
    "activities",
    "facilities",
    "age_range",
    "level",
    "accessibility_support",
    "gender_restriction",
)


@dataclass(frozen=True, slots=True)
class ComplianceRow:
    """One assessed feed with its custom properties, if the snapshot reported them.

    `properties` is the feed's custom-property row, or None when it uses none or when the
    custom-property read failed. `properties_reported` tells those two apart.
    """

    quality: QualityRow
    properties: PropertyRow | None
    properties_reported: bool

    @property
    def feed_id(self) -> str:
        return self.quality.feed_id

    @property
    def feed_name(self) -> str:
        return self.quality.feed_name

    @property
    def publisher_name(self) -> str:
        return self.quality.publisher_name

    @property
    def dataset_name(self) -> str:
        return self.quality.dataset_name

    @property
    def dataset_key(self) -> str:
        return self.quality.dataset_key

    @property
    def feed_type(self) -> str:
        return self.quality.feed_type

    @property
    def feed_version(self) -> str:
        return self.quality.feed_version

    @property
    def missing_required_fields(self) -> Mapping[str, tuple[str, ...]]:
        return self.quality.missing_required_fields

    @property
    def missing_required_count(self) -> int:
        """Distinct required fields missing, across all of the feed's opportunity types."""
        return len(
            {field for fields in self.missing_required_fields.values() for field in fields}
        )

    @property
    def key_completeness(self) -> dict[str, float | None]:
        """Each key field's completeness, 0-100. None where the assessment did not reach it,
        which is not the same as zero."""
        values = self.quality.completeness
        return {field: values.get(field) for field in KEY_FIELDS}

    @property
    def mean_key_completeness(self) -> float | None:
        return quality.mean_completeness(self.key_completeness)

    @property
    def custom_property_count(self) -> int | None:
        """Distinct non-standard properties. Zero when the feed uses none, None when the
        custom-property read failed or the snapshot did not count them."""
        if not self.properties_reported:
            return None
        if self.properties is None:
            return 0
        return self.properties.property_count

    @property
    def outside_beta_count(self) -> int | None:
        """Non-standard properties outside the OpenActive beta namespace."""
        if not self.properties_reported:
            return None
        return self.properties.outside_beta_count if self.properties else 0


def build_rows(snapshot: SchemaComplianceSnapshot) -> tuple[ComplianceRow, ...]:
    """One row per feed in the quality snapshot, joined to its custom properties by id.

    A feed that appears only in the custom-property snapshot is left out: it has no
    quality assessment, so there is no version or required-field check to show for it.
    """
    reported = snapshot.custom_properties is not None
    by_feed: dict[str, PropertyRow] = {}
    if snapshot.custom_properties is not None:
        for feed in snapshot.custom_properties.data:
            if feed.feed_id:
                by_feed[feed.feed_id] = schema_drift.build_row(feed)
    return tuple(
        ComplianceRow(
            quality=row,
            properties=by_feed.get(row.feed_id),
            properties_reported=reported,
        )
        for row in quality.build_rows(snapshot.quality.data)
    )
