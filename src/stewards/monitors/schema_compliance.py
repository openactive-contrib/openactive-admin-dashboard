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

Each row then gets one verdict:

- **Fail**: a required field is missing. The feed breaks the standard.
- **Warning**: nothing required is missing, but a key field is poorly covered, the
  completeness was not reported, or the version is not known.
- **Pass**: everything required is there and the key fields are well covered.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from statistics import fmean
from typing import Any

import altair as alt
import pandas as pd

from stewards.api.models import SchemaComplianceSnapshot, VocabularyAdherence
from stewards.monitors import quality, schema_drift
from stewards.monitors.health import Health, HealthState, Movement
from stewards.monitors.overview import Fact, TileCard
from stewards.monitors.quality import UNKNOWN, QualityRow, Stat, format_number
from stewards.monitors.registry import Col
from stewards.monitors.schema_drift import PropertyRow
from stewards.monitors.thresholds import Tone, band_tone, score_tone
from stewards.monitors.transforms import EMPTY, format_cell

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

#: A key field below this share of items is poorly covered. The same line as the amber band
#: in `thresholds.score_tone`, so a field shaded red in the table is the one that warns.
FIELD_WARNING_BELOW = 60.0

#: A vocabulary matching fewer than this share of a feed's items is poorly covered.
#: Deliberately a higher bar than `FIELD_WARNING_BELOW`, and a separate constant so the two
#: can move apart: a thinly populated field is one of many things a steward might chase,
#: whereas an unrecognised activity term means the session does not surface in a search at
#: all. The number is the standard's to set, not this module's to infer.
VOCABULARY_WARNING_BELOW = 90.0

#: A column field that reads one key field's completeness, e.g. `completeness.location`.
COMPLETENESS_PREFIX = "completeness."


class Verdict(StrEnum):
    """The row's verdict. The values are status tokens `thresholds` already tones."""

    FAIL = "fail"
    WARNING = "warning"
    PASS = "pass"


#: Worst first, for sorting.
VERDICT_ORDER = {Verdict.FAIL: 0, Verdict.WARNING: 1, Verdict.PASS: 2}


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
    def feed_url(self) -> str:
        return self.quality.feed_url

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
    def missing_required_label(self) -> str:
        """The missing required fields as one cell, alphabetically.

        Distinct across opportunity types, the way `missing_required_count` counts them: a
        field absent from both SessionSeries and FacilityUse is one field, and naming it
        twice beside a count of one would read as a bug. Which type omits what is the row
        pop-up's job — see `missing_field_lines`.
        """
        names = {f for fields in self.missing_required_fields.values() for f in fields}
        return ", ".join(sorted(names, key=str.lower)) or EMPTY

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
    def activity_vocabulary(self) -> VocabularyAdherence | None:
        return self.quality.activity_vocabulary

    @property
    def facility_vocabulary(self) -> VocabularyAdherence | None:
        return self.quality.facility_vocabulary

    @property
    def activity_adherence(self) -> float | None:
        """Share of the feed's activity terms matching the standard vocabulary, 0-100.

        None where the assessment's five-day window did not reach this feed, which is not
        the same as nothing matching. The window fills in over time, so most feeds read
        None today.
        """
        return self.activity_vocabulary.adherence_pct if self.activity_vocabulary else None

    @property
    def facility_adherence(self) -> float | None:
        """As `activity_adherence`, for the feed's facility terms."""
        return self.facility_vocabulary.adherence_pct if self.facility_vocabulary else None

    @property
    def vocabularies_reported(self) -> bool:
        """Whether the assessment reported either figure for this feed."""
        return self.activity_adherence is not None or self.facility_adherence is not None

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
    def custom_property_label(self) -> str:
        """The feed's non-standard properties as one cell, alphabetically.

        Blank for a feed that publishes none and for a failed custom-property read alike.
        The caption above the table already tells those two apart, as it does for the count
        beside this, and a cell cannot say it twice.
        """
        return self.properties.property_label if self.properties else EMPTY

    @property
    def outside_beta_count(self) -> int | None:
        """Non-standard properties outside the OpenActive beta namespace."""
        if not self.properties_reported:
            return None
        return self.properties.outside_beta_count if self.properties else 0

    @property
    def weak_fields(self) -> tuple[str, ...]:
        """Key fields the assessment reached and found below `FIELD_WARNING_BELOW`."""
        return tuple(
            field
            for field, value in self.key_completeness.items()
            if value is not None and value < FIELD_WARNING_BELOW
        )

    @property
    def weak_vocabularies(self) -> tuple[str, ...]:
        """Vocabularies the assessment reported and found below `VOCABULARY_WARNING_BELOW`.

        A vocabulary it did not report is absent from this tuple rather than weak. That
        asymmetry with `weak_fields` is the point: the quality assessment scores every key
        field, so a missing completeness figure is itself worth reporting, but the
        vocabulary job works a rolling five-day window and has not reached most feeds yet.
        Warning on that silence would fail the fleet and then quietly un-fail it.
        """
        reported = (
            ("activities", self.activity_adherence),
            ("facilities", self.facility_adherence),
        )
        return tuple(
            name for name, pct in reported if pct is not None and pct < VOCABULARY_WARNING_BELOW
        )

    @property
    def reasons(self) -> tuple[str, ...]:
        """Why the row is not a pass, worst first, in plain words. Empty for a pass."""
        lines: list[str] = []
        if count := self.missing_required_count:
            noun = "field" if count == 1 else "fields"
            lines.append(f"{count} required {noun} missing")
        if self.mean_key_completeness is None:
            lines.append("Completeness of the key fields was not reported")
        if weak := self.weak_fields:
            labels = ", ".join(quality.field_label(field).lower() for field in weak)
            lines.append(f"Below {FIELD_WARNING_BELOW:.0f}% complete: {labels}")
        if weak_vocabularies := self.weak_vocabularies:
            lines.append(
                f"Below {VOCABULARY_WARNING_BELOW:.0f}% vocabulary match: "
                f"{', '.join(weak_vocabularies)}"
            )
        if self.feed_version == UNKNOWN:
            lines.append("Feed version not reported")
        return tuple(lines)

    @property
    def verdict(self) -> Verdict:
        if self.missing_required_count:
            return Verdict.FAIL
        if self.reasons:
            return Verdict.WARNING
        return Verdict.PASS

    @property
    def status(self) -> str:
        """The verdict as a status token, for a `ColKind.STATUS` column and the filters."""
        return self.verdict.value


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


# --- reading, sorting and filtering -------------------------------------------------------


def resolve(row: ComplianceRow, field: str) -> Any:
    """Read a column or filter field off a row. `completeness.<field>` reads one key
    field's completeness; anything else is an attribute of the row."""
    if field.startswith(COMPLETENESS_PREFIX):
        return row.key_completeness.get(field.removeprefix(COMPLETENESS_PREFIX))
    return getattr(row, field, None)


def sort_rows(rows: Sequence[ComplianceRow]) -> list[ComplianceRow]:
    """Worst first: fails, then warnings, then passes. Within each, the most required
    fields missing first, then the least complete, then by publisher and feed."""

    def key(row: ComplianceRow) -> tuple[int, int, float, str, str]:
        mean = row.mean_key_completeness
        return (
            VERDICT_ORDER[row.verdict],
            -row.missing_required_count,
            mean if mean is not None else -1.0,
            row.publisher_name.lower(),
            row.feed_name.lower(),
        )

    return sorted(rows, key=key)


def filter_options(rows: Sequence[ComplianceRow], field: str) -> list[str]:
    """Distinct non-empty values for a filter field, sorted, for a selectbox."""
    return sorted(
        {str(value) for row in rows if (value := resolve(row, field)) not in (None, "")}
    )


def search_rows(rows: Iterable[ComplianceRow], term: str) -> list[ComplianceRow]:
    """Case-insensitive match on publisher, dataset, feed name, feed id, the missing
    required fields and the custom properties.

    The last two are what the table's two name columns show, so a steward can type
    `location` or `beta:video` and get every feed the column would have them scroll for.
    The retired drift page searched its property names the same way.
    """
    needle = term.strip().lower()
    if not needle:
        return list(rows)
    return [
        row
        for row in rows
        if needle
        in " ".join(
            part.lower()
            for part in (
                row.publisher_name,
                row.dataset_name,
                row.feed_name,
                row.feed_id,
                row.missing_required_label,
                row.custom_property_label,
            )
        )
    ]


def apply_filters(
    rows: Sequence[ComplianceRow],
    *,
    search: str = "",
    selections: Mapping[str, str] | None = None,
    not_passing_only: bool = False,
) -> list[ComplianceRow]:
    """Search, per-field selections and the "not passing" toggle, in that order."""
    result = search_rows(rows, search)
    for field, wanted in (selections or {}).items():
        if not wanted:
            continue
        result = [row for row in result if str(resolve(row, field) or "") == wanted]
    if not_passing_only:
        result = [row for row in result if row.verdict is not Verdict.PASS]
    return result


# --- the table ----------------------------------------------------------------------------


def to_dataframe(columns: Sequence[Col], rows: Sequence[ComplianceRow]) -> pd.DataFrame:
    """One line per row, in the column order given. Zero rows gives an empty frame with
    the columns, so the table renders empty instead of raising."""
    labels = [col.label for col in columns]
    records = [
        {col.label: format_cell(col, resolve(row, col.field)) for col in columns}
        for row in rows
    ]
    return pd.DataFrame(records, columns=labels)


def tone_frame(columns: Sequence[Col], rows: Sequence[ComplianceRow]) -> pd.DataFrame:
    """Tone name per cell, aligned with `to_dataframe`. Uses the quality page's cell
    tones, so completeness is shaded on the same bands everywhere."""
    labels = [col.label for col in columns]
    records = [
        {
            col.label: (
                tone.value if (tone := quality.cell_tone(col, resolve(row, col.field))) else ""
            )
            for col in columns
        }
        for row in rows
    ]
    return pd.DataFrame(records, columns=labels).fillna("")


# --- the figures --------------------------------------------------------------------------


def _share(part: int, whole: int) -> str:
    return f"{part / whole:.0%} of feeds checked" if whole else EMPTY


def _vocabulary_tone(pct: float | None) -> Tone | None:
    """The figure above the table, shaded on the same bar as the cells beneath it.

    Not `score_tone`: that bands at 80 and 60, so an 85% average would read green while
    every row carrying it warns.
    """
    return None if pct is None else band_tone(pct, VOCABULARY_WARNING_BELOW)


def _count_tone(count: int, tone: Tone) -> Tone:
    """A zero is nothing to flag.

    Narrower than `quality._count_tone`, which shades a figure the batch may not report:
    every count here is derived from the rows, so there is no "not reported" case.
    """
    return tone if count else Tone.GREY


def key_field_averages(rows: Sequence[ComplianceRow]) -> dict[str, float | None]:
    """Each key field's mean completeness across the feeds that reported it."""
    averages: dict[str, float | None] = {}
    for field in KEY_FIELDS:
        values = [v for row in rows if (v := row.key_completeness.get(field)) is not None]
        averages[field] = fmean(values) if values else None
    return averages


def page_stats(rows: Sequence[ComplianceRow]) -> tuple[Stat, ...]:
    """The six figures above the table.

    Worked out from the rows rather than read from a summary block, because the page joins
    two snapshots and neither block describes the join. Pass in every row, not the
    filtered ones, so a filter does not appear to move the figures.
    """
    total = len(rows)
    failing = sum(row.verdict is Verdict.FAIL for row in rows)
    warning = sum(row.verdict is Verdict.WARNING for row in rows)
    means = [m for row in rows if (m := row.mean_key_completeness) is not None]
    average = fmean(means) if means else None
    reported = bool(rows) and rows[0].properties_reported
    custom = sum(bool(row.custom_property_count) for row in rows) if reported else None

    # Both vocabularies pooled: a feed reports activities or facilities depending on what it
    # publishes, so averaging them separately would compare two different populations.
    adherences = [
        pct
        for row in rows
        for pct in (row.activity_adherence, row.facility_adherence)
        if pct is not None
    ]
    vocabulary = fmean(adherences) if adherences else None
    assessed = sum(row.vocabularies_reported for row in rows)

    return (
        Stat("Feeds checked", format_number(total), None, "from the quality assessment"),
        Stat(
            "Failing",
            format_number(failing),
            _count_tone(failing, Tone.RED),
            "missing a required field",
        ),
        Stat(
            "Warnings",
            format_number(warning),
            _count_tone(warning, Tone.AMBER),
            "key fields or vocabularies poorly covered",
        ),
        Stat(
            "Key field completeness",
            EMPTY if average is None else f"{average:.0f}%",
            None if average is None else score_tone(average),
            "average across the key fields",
        ),
        Stat(
            "Vocabulary match",
            EMPTY if vocabulary is None else f"{vocabulary:.0f}%",
            _vocabulary_tone(vocabulary),
            (
                f"mean across {format_number(assessed)} of {format_number(total)} feeds"
                if vocabulary is not None
                else "not reported for any feed yet"
            ),
        ),
        Stat(
            "Non-standard properties",
            format_number(custom),
            None,
            _share(custom, total) if custom is not None else "custom properties not reported",
        ),
    )


# --- the chart -----------------------------------------------------------------------------


def key_field_chart(
    rows: Sequence[ComplianceRow],
    palette: Mapping[Tone, str],
    *,
    label_colour: str,
    grid_colour: str,
) -> alt.Chart | None:
    """Mean completeness of each key field across the feeds, in `KEY_FIELDS` order, shaded
    on the score scale. A field no feed reported is left off rather than drawn as zero."""
    reported = [(f, v) for f, v in key_field_averages(rows).items() if v is not None]
    return quality._bar_chart(
        [quality.field_label(field) for field, _ in reported],
        [float(value) for _, value in reported],
        [palette[score_tone(value)] for _, value in reported],
        label_title="Field",
        value_title="Mean completeness %",
        label_colour=label_colour,
        grid_colour=grid_colour,
        horizontal=True,
        height=260,
    )


# --- the selected row ----------------------------------------------------------------------


def key_field_frame(row: ComplianceRow) -> pd.DataFrame:
    """The selected feed's completeness per key field, in `KEY_FIELDS` order. A field the
    assessment did not reach stays as an empty cell, not a zero."""
    return pd.DataFrame(
        [
            {
                "Field": quality.field_label(field),
                "Completeness": None if value is None else float(value),
            }
            for field, value in row.key_completeness.items()
        ],
        columns=["Field", "Completeness"],
    )


#: Said in the pop-up for a feed the assessment's five-day window has not reached. Not
#: "no match": the figures are absent, not zero.
NO_VOCABULARY_FIGURES = (
    "The vocabulary assessment has not reached this feed yet, so it reports no figures."
)

#: The vocabulary breakdown's count columns, in the order the pop-up reads them: how the
#: assessment matched a term, from the cleanest match to no match at all.
VOCABULARY_COUNTS = (
    ("valid_id", "Matched by id"),
    ("label_match_only", "Label only"),
    ("other_scheme", "Other scheme"),
    ("invalid_id", "Invalid id"),
    ("unrecognised", "Unrecognised"),
)

#: The breakdown's columns, in order.
VOCABULARY_COLUMNS = ("Vocabulary", "Match", "Items", *(head for _, head in VOCABULARY_COUNTS))


def vocabulary_frame(row: ComplianceRow) -> pd.DataFrame:
    """How the selected feed's activity and facility terms matched, one line per vocabulary.

    Only the vocabularies the assessment reported: a feed it did not reach has no line at
    all, rather than a line of zeros. A count it did not report stays an empty cell for the
    same reason.
    """
    reported = (
        ("Activities", row.activity_vocabulary),
        ("Facilities", row.facility_vocabulary),
    )
    records = [
        {
            "Vocabulary": label,
            "Match": adherence.adherence_pct,
            "Items": adherence.items,
            **{head: getattr(adherence, attr) for attr, head in VOCABULARY_COUNTS},
        }
        for label, adherence in reported
        if adherence is not None
    ]
    return pd.DataFrame(records, columns=list(VOCABULARY_COLUMNS))


#: Full adherence. A vocabulary at this has nothing left to name.
FULL_ADHERENCE = 100.0

#: Said for a vocabulary that fell short but whose assessment named no terms. Saying nothing
#: would read as "nothing to fix", and two of today's activity assessments are in that state.
NONE_NAMED = "no terms named in this snapshot"


def unmatched_terms(row: ComplianceRow) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Each vocabulary that fell short, with the terms the assessment could not match.

    The counts say how badly a feed did; these say what to go and fix, which is why they are
    worth the space. Read them with the counts above: the same term list means one thing
    beside a high `label_match_only` — the right activities in the wrong encoding, a line of
    mapping code — and another beside a high `unrecognised`, where the feed is publishing
    room codes or formats where activities belong.

    A vocabulary at full adherence is left out; one that fell short but named nothing is kept
    with an empty tuple, because the caller must say so rather than fall silent. Terms come
    back exactly as the feed published them, for the caller to render verbatim.
    """
    found: list[tuple[str, tuple[str, ...]]] = []
    for label, vocabulary in (
        ("Activities", row.activity_vocabulary),
        ("Facilities", row.facility_vocabulary),
    ):
        if vocabulary is None:
            continue
        if vocabulary.top_unmatched:
            found.append((label, vocabulary.top_unmatched))
            continue
        adherence = vocabulary.adherence_pct
        if adherence is not None and adherence < FULL_ADHERENCE:
            found.append((label, ()))
    return tuple(found)


def missing_field_lines(row: ComplianceRow) -> tuple[str, ...]:
    """`SessionSeries: name, location` per opportunity type with a field missing."""
    return quality.missing_field_lines(row.quality)


# --- the overview card ---------------------------------------------------------------------

#: What the card says when the quality snapshot carried no feeds at all.
NOT_ASSESSED = "no feeds assessed in this snapshot"


def assess_compliance(rows: Sequence[ComplianceRow]) -> Health:
    """The card's verdict, from the share of feeds that do not break the standard.

    Two rules, in order. The level comes from the compliant share on `thresholds.score_tone`
    — the bands that shade every other percentage in the app, so the card and the table
    cannot tell different stories. A clean share is then held back from `HEALTHY` while any
    feed falls short of the key fields: a thin recommended field is not a breach of the
    standard, but it is not an all-clear either.

    `movement` stays unknown for the reason `quality.assess_quality` gives: one snapshot
    carries a level and says nothing about direction.
    """
    if not rows:
        return Health(HealthState.UNKNOWN, Movement.UNKNOWN, NOT_ASSESSED)

    failing = sum(row.verdict is Verdict.FAIL for row in rows)
    warning = sum(row.verdict is Verdict.WARNING for row in rows)
    total = len(rows)
    states = {Tone.GREEN: HealthState.HEALTHY, Tone.AMBER: HealthState.WARNING}
    state = states.get(score_tone((total - failing) / total * 100), HealthState.CRITICAL)
    if state is HealthState.HEALTHY and warning:
        state = HealthState.WARNING

    if failing:
        noun = "feed" if total == 1 else "feeds"
        reason = (
            f"{format_number(failing)} of {format_number(total)} {noun} missing a "
            "required field"
        )
    elif warning:
        reason = (
            f"{format_number(warning)} of {format_number(total)} feeds fall short on "
            "key fields or vocabularies"
        )
    else:
        reason = f"all {format_number(total)} feeds meet the standard"
    return Health(state, Movement.UNKNOWN, reason, current=float(failing))


def tile_note(rows: Sequence[ComplianceRow]) -> str:
    if not rows:
        return NOT_ASSESSED
    return f"of {format_number(len(rows))} feeds assessed · this snapshot only"


def tile_card(snapshot: SchemaComplianceSnapshot) -> TileCard:
    """The overview card: feeds failing, three supporting figures and the verdict.

    It takes the snapshot rather than the rows because the join is what the card is about:
    `/summary` describes neither read, let alone the two together. See `tile_viz.Facts`.
    """
    rows = build_rows(snapshot)
    failing = sum(row.verdict is Verdict.FAIL for row in rows)
    warning = sum(row.verdict is Verdict.WARNING for row in rows)
    means = [mean for row in rows if (mean := row.mean_key_completeness) is not None]
    average = fmean(means) if means else None
    return TileCard(
        value=format_number(failing),
        unit="feeds failing",
        health=assess_compliance(rows),
        facts=(
            Fact("Feeds checked", format_number(len(rows))),
            Fact("Warnings", format_number(warning), _count_tone(warning, Tone.AMBER)),
            Fact(
                "Key fields",
                EMPTY if average is None else f"{average:.0f}%",
                None if average is None else score_tone(average),
            ),
        ),
        note=tile_note(rows),
        badge=failing,
    )
