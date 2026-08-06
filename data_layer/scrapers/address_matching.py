"""
    PropIQ - Shared Address Matching
    Used by every "match a third-party listing to an existing real Property
    row" enrichment script (Redfin, Zillow, ...) so the matching logic -
    and its failure modes - live in exactly one place.

    @author Minh Thang Nguyen
    @version August 5, 2026
"""

from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy import select

from data_layer.models.database import Property

_STREET_SUFFIX_MAP = {
    'STREET': 'ST', 'AVENUE': 'AVE', 'DRIVE': 'DR', 'ROAD': 'RD',
    'LANE': 'LN', 'COURT': 'CT', 'PLACE': 'PL', 'BOULEVARD': 'BLVD',
    'CIRCLE': 'CIR', 'TERRACE': 'TER', 'HIGHWAY': 'HWY',
    'PARKWAY': 'PKWY', 'TRAIL': 'TRL', 'SQUARE': 'SQ',
}
_UNIT_SUFFIX = re.compile(r'\s+(APT|UNIT|STE|SUITE|#)\s*\S+$', re.IGNORECASE)
_PUNCTUATION = re.compile(r'[.,]')
_WHITESPACE = re.compile(r'\s+')


def normalize_address(raw: str | None, city_hint: str | None = None) -> str | None:
    """
    Collapse an address down to a comparable (house-number + street) key.
    OC's SITE_ADDRESS sometimes has the city name appended (e.g. "4884
    MAIN ST YORBA LINDA"); third-party sources (Redfin, Zillow) never do -
    strip a trailing city name if we know it. Deliberately exact-match, not
    fuzzy: trades recall for zero false-positive risk - a wrong match would
    silently attach one property's real price to a different parcel, which
    is worse than just leaving a parcel unpriced.
    """
    if not raw:
        return None
    s = _PUNCTUATION.sub('', raw.upper().strip())
    s = _WHITESPACE.sub(' ', s)

    if city_hint:
        city_upper = city_hint.upper().strip()
        if city_upper and s.endswith(city_upper):
            s = s[: -len(city_upper)].strip()

    s = _UNIT_SUFFIX.sub('', s).strip()
    s = ' '.join(_STREET_SUFFIX_MAP.get(p, p) for p in s.split(' '))
    return s or None


def build_zip_index(session, zip_code: str, source: str) -> dict[str, list[Property]]:
    """normalized address -> matching real Property rows in this zip, for
    properties that came from the given data_source (e.g. 'oc_parcel_gis').
    Restricting by source keeps enrichment from ever touching synthetic
    seed rows, which the AVM's leakage-free training relies on staying
    exactly as generated."""
    rows = session.execute(
        select(Property).where(
            Property.zip_code == zip_code,
            Property.data_source == source,
        )
    ).scalars().all()

    index: dict[str, list[Property]] = defaultdict(list)
    for prop in rows:
        key = normalize_address(prop.address, city_hint=prop.city)
        if key:
            index[key].append(prop)
    return index