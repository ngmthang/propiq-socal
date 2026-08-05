"""
    PropIQ - Improvement Recommendation Engine
    For a property, evaluates feasible value-adding interventions (ADU, JADU,
    SB 9 lot split, sqft/bed/bath additions, garage conversion, pool),
    estimates cost and value lift, and ranks them.

    Two estimation methods - every Recommendation says which it used:
        - "avm_diff": clone the property's real feature dict (sourced from
          PropertyFeature, NOT the ORM getattr fallback that silently
          defaults development_score/adu_eligible/underbuilt_ratio), perturb
          the raw inputs the intervention changes, valuate twice, diff.
        - "rule_of_thumb": for interventions with no corresponding AVM input
          (pool, JADU with no sqft delta, a brand-new SB 9 parcel) - a flat
          market-rate estimate, explicitly flagged as not model-derived.

    Only feasible recommendations with a computed lift are returned by
    recommend() - infeasible ones are evaluated (for the feasibility_reason)
    but dropped, so the frontend card never renders on a missing number.

    @author Minh Thang Nguyen
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from typing import TYPE_CHECKING

from data_layer.models.database import Property

if TYPE_CHECKING:
    from .engine import InferenceEngine

# --- Cost / value constants -------------------------------------------------
# Rough SoCal planning-level figures, not bids or comps. Tune freely.

ADU_COST_PER_SQFT = 350
JADU_FLAT_COST = 40_000
ADDITION_COST_PER_SQFT = 300
GARAGE_CONVERSION_COST = 30_000
POOL_COST = 65_000
BATH_ADD_COST = 25_000
SB9_SPLIT_COST = 60_000

POOL_VALUE_LIFT_PCT = 3.0
JADU_VALUE_LIFT_PCT = 2.5
SB9_LAND_VALUE_FRACTION = 0.15  # rough share of neighborhood median attributable to a new buildable lot
SB9_MAX_LIFT_PCT = 20.0

AVM_CAVEAT = "AVM trained on synthetic data - directional estimate, not an appraisal."


@dataclass
class Recommendation:
    type: str
    title: str
    rationale: str
    feasible: bool
    feasibility_reason: str
    est_cost: Optional[float] = None
    value_lift_pct: Optional[float] = None
    confidence: float = 0.0
    method: Optional[str] = None  # "avm_diff" | "rule_of_thumb" | None
    caveat: Optional[str] = None


class ImprovementRecommender:
    def __init__(self, engine: "InferenceEngine"):
        self.engine = engine

    def recommend(self, prop: Property, top_n: int = 6) -> list[Recommendation]:
        from .engine import InsufficientDataError

        base_dict = self._base_feature_dict(prop)
        base_value = None
        if base_dict is not None:
            try:
                base_value = self.engine.valuate(dict(base_dict)).estimated_value
            except ValueError:
                base_value = None

        feat = prop.features  # PropertyFeature row, may be None

        candidates = [
            self._adu(prop, feat, base_dict, base_value),
            self._sqft_addition(prop, feat, base_dict, base_value),
            self._bed_bath_addition(prop, feat, base_dict, base_value),
            self._garage_conversion(prop, feat, base_dict, base_value),
            self._pool(prop, base_value),
            self._jadu(prop, feat, base_value),
            self._sb9_split(prop, base_value),
        ]

        shown = [r for r in candidates if r.feasible and r.value_lift_pct is not None]
        shown.sort(key=lambda r: r.value_lift_pct, reverse=True)
        return shown[:top_n]

    # --- feature dict construction ------------------------------------------

    def _base_feature_dict(self, prop: Property) -> Optional[dict]:
        """Raw-input dict FeatureBuilder expects, sourced from the REAL
        PropertyFeature row - not InferenceEngine._as_dict's ORM getattr,
        which silently defaults development_score/adu_eligible/
        underbuilt_ratio since those columns live on PropertyFeature, not
        Property. Returns None if the property lacks physical AVM inputs."""
        if not prop.is_avm_ready:
            return None

        feat = prop.features
        nb = prop.neighborhood

        d = {
            "id": prop.id,
            "zip_code": prop.zip_code,
            "latitude": prop.latitude,
            "longitude": prop.longitude,
            "bedrooms": prop.bedrooms,
            "bathrooms": prop.bathrooms,
            "building_sqft": prop.building_sqft,
            "lot_size_sqft": prop.lot_size_sqft,
            "year_built": prop.year_built,
            "list_price": prop.list_price,
        }

        if feat is not None:
            d.update({
                "walk_score": feat.walk_score,
                "transit_score": feat.transit_score,
                "school_rating": feat.school_rating,
                "development_score": feat.development_score,
                "adu_eligible": int(bool(feat.adu_eligible)),
                "far_ratio": feat.far_ratio,
            })

        if nb is not None:
            d.update({
                "neighborhood_median_price": nb.median_home_price,
                "price_change_yoy": nb.price_change_yoy,
                "days_on_market": nb.avg_days_on_market,
            })

        return {k: v for k, v in d.items() if v is not None}

    def _diff_value(self, base_dict, base_value, **overrides) -> Optional[float]:
        if base_dict is None or not base_value:
            return None
        after = dict(base_dict)
        after.update({k: v for k, v in overrides.items() if v is not None})
        try:
            after_value = self.engine.valuate(after).estimated_value
        except ValueError:  # InsufficientDataError is a ValueError subclass
            return None
        return round(float(after_value - base_value) / float(base_value) * 100, 2)

    # --- individual intervention evaluators ---------------------------------

    def _adu(self, prop, feat, base_dict, base_value) -> Recommendation:
        eligible = bool(feat and feat.adu_eligible)
        reason = (
            "Lot size and zoning meet ADU eligibility (CA AB 68 / SB 9)."
            if eligible else
            "Not flagged ADU-eligible - lot likely under 1,200 sqft or non-residential zoning."
        )
        if not eligible:
            return Recommendation("adu", "Add an ADU", reason, False, reason)

        adu_sqft = int(feat.adu_max_sqft) if feat.adu_max_sqft else min(
            1200, int((prop.building_sqft or 1500) * 0.5)
        )
        overrides = {}
        if base_dict:
            overrides = {
                "building_sqft": base_dict.get("building_sqft", 0) + adu_sqft,
                "bedrooms": base_dict.get("bedrooms", 0) + 1,
                "bathrooms": base_dict.get("bathrooms", 0) + 1,
            }
        lift = self._diff_value(base_dict, base_value, **overrides)
        return Recommendation(
            "adu", f"Add a {adu_sqft} sqft ADU",
            f"Lot qualifies for a detached ADU up to {adu_sqft} sqft under CA state law.",
            True, reason,
            est_cost=adu_sqft * ADU_COST_PER_SQFT,
            value_lift_pct=lift, confidence=0.55 if lift is not None else 0.0,
            method="avm_diff" if lift is not None else None,
            caveat=AVM_CAVEAT if lift is not None else None,
        )

    def _sqft_addition(self, prop, feat, base_dict, base_value) -> Recommendation:
        far, lot, bldg = (feat.far_ratio if feat else None), prop.lot_size_sqft, prop.building_sqft
        headroom = int(far * lot - bldg) if (far and lot and bldg) else None
        feasible = bool(headroom and headroom >= 200)
        reason = (
            f"FAR cap allows ~{headroom} more sqft on this lot." if headroom is not None else
            "FAR/setback data not available for this property - feasibility unconfirmed."
        )
        if not feasible:
            return Recommendation("sqft_addition", "Add square footage", reason, False, reason)

        add_sqft = min(headroom, 600)
        overrides = {"building_sqft": base_dict.get("building_sqft", 0) + add_sqft} if base_dict else {}
        lift = self._diff_value(base_dict, base_value, **overrides)
        return Recommendation(
            "sqft_addition", f"Add {add_sqft} sqft of living space", reason, True, reason,
            est_cost=add_sqft * ADDITION_COST_PER_SQFT,
            value_lift_pct=lift, confidence=0.5 if lift is not None else 0.0,
            method="avm_diff" if lift is not None else None,
            caveat=AVM_CAVEAT if lift is not None else None,
        )

    def _bed_bath_addition(self, prop, feat, base_dict, base_value) -> Recommendation:
        beds, bldg = prop.bedrooms or 0, prop.building_sqft or 0
        sqft_per_bed = bldg / max(beds, 1)
        feasible = sqft_per_bed > 700
        reason = (
            f"~{int(sqft_per_bed)} sqft per bedroom suggests convertible space for an extra bath."
            if feasible else
            "Existing sqft-per-bedroom is already tight - no obvious room to convert."
        )
        if not feasible:
            return Recommendation("bed_bath_addition", "Add a bathroom", reason, False, reason)

        overrides = {"bathrooms": base_dict.get("bathrooms", 0) + 1} if base_dict else {}
        lift = self._diff_value(base_dict, base_value, **overrides)
        return Recommendation(
            "bed_bath_addition", "Convert existing space into a bathroom", reason, True, reason,
            est_cost=BATH_ADD_COST,
            value_lift_pct=lift, confidence=0.45 if lift is not None else 0.0,
            method="avm_diff" if lift is not None else None,
            caveat=AVM_CAVEAT if lift is not None else None,
        )

    def _garage_conversion(self, prop, feat, base_dict, base_value) -> Recommendation:
        has_garage = bool(prop.garage_spaces and prop.garage_spaces > 0)
        reason = (
            f"{prop.garage_spaces}-space garage present and convertible."
            if has_garage else "No garage on record to convert."
        )
        if not has_garage:
            return Recommendation("garage_conversion", "Convert garage to living space", reason, False, reason)

        convert_sqft = 400
        overrides = {"building_sqft": base_dict.get("building_sqft", 0) + convert_sqft} if base_dict else {}
        lift = self._diff_value(base_dict, base_value, **overrides)
        return Recommendation(
            "garage_conversion", "Convert garage to living space",
            reason + " Note: removes covered parking, which some buyers value.",
            True, reason,
            est_cost=GARAGE_CONVERSION_COST,
            value_lift_pct=lift, confidence=0.4 if lift is not None else 0.0,
            method="avm_diff" if lift is not None else None,
            caveat=(AVM_CAVEAT + " Doesn't account for a lost-parking discount some buyers apply.")
                   if lift is not None else None,
        )

    def _pool(self, prop, base_value) -> Recommendation:
        has_pool = bool(prop.pool)
        lot = prop.lot_size_sqft or 0
        feasible = (not has_pool) and lot >= 4000
        reason = (
            "Already has a pool." if has_pool else
            "Lot size supports a pool." if feasible else
            "Lot likely too small for a standard in-ground pool."
        )
        if not feasible:
            return Recommendation("pool", "Add a pool", reason, False, reason)

        lift = POOL_VALUE_LIFT_PCT if base_value else None
        return Recommendation(
            "pool", "Add an in-ground pool", reason, True, reason,
            est_cost=POOL_COST, value_lift_pct=lift, confidence=0.3,
            method="rule_of_thumb" if lift is not None else None,
            caveat="Not modeled by the valuation AVM (no pool feature in training data) - "
                   "flat market-rate estimate only." if lift is not None else None,
        )

    def _jadu(self, prop, feat, base_value) -> Recommendation:
        eligible = bool(feat and feat.adu_eligible) and (prop.building_sqft or 0) >= 800
        reason = (
            "Zoning allows a JADU and there's enough existing sqft to convert a room."
            if eligible else "Zoning ineligible or building too small to convert a room."
        )
        if not eligible:
            return Recommendation("jadu", "Add a JADU (converted room)", reason, False, reason)

        lift = JADU_VALUE_LIFT_PCT if base_value else None
        return Recommendation(
            "jadu", "Convert a room into a Junior ADU", reason, True, reason,
            est_cost=JADU_FLAT_COST, value_lift_pct=lift, confidence=0.3,
            method="rule_of_thumb" if lift is not None else None,
            caveat="Not modeled by the valuation AVM (no sqft change) - flat market-rate estimate only."
                   if lift is not None else None,
        )

    def _sb9_split(self, prop, base_value) -> Recommendation:
        lot = prop.lot_size_sqft or 0
        is_sfr = prop.property_type is not None and prop.property_type.value == "single_family"
        feasible = is_sfr and lot >= 2400
        reason = (
            f"{int(lot)} sqft lot, single-family zoning - splits into two parcels of "
            f"~{int(lot/2)} sqft each, above SB 9's 1,200 sqft minimum."
            if feasible else "Lot too small or not single-family zoned for an SB 9 split."
        )
        if not feasible:
            return Recommendation("sb9_split", "SB 9 lot split", reason, False, reason)

        nb = prop.neighborhood
        median = nb.median_home_price if nb else None
        lift = None
        if median and base_value:
            lift = min(
                round(float(median) * SB9_LAND_VALUE_FRACTION / float(base_value) * 100, 2),
                SB9_MAX_LIFT_PCT,
            )

        return Recommendation(
            "sb9_split", "Split lot under SB 9",
            reason + " New parcel can be sold or built on separately.",
            True, reason,
            est_cost=SB9_SPLIT_COST, value_lift_pct=lift,
            confidence=0.25 if lift is not None else 0.0,
            method="rule_of_thumb" if lift is not None else None,
            caveat="Not an AVM output - the new parcel has no comparable trained sale data, "
                   "so this is a rough land-value share of the neighborhood median, not a "
                   "model prediction." if lift is not None else None,
        )