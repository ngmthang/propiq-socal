"""
    Regression tests for the property improvement recommendation engine.

    Two bugs from the original build session are specifically guarded here:
    1. value_lift_pct returned as np.float32 instead of a plain Python float
       (JSON-serialization risk through FastAPI/Pydantic - see engine.py's
       valuate() float(pred) fix).
    2. SB9_LAND_VALUE_FRACTION at 0.35 produced an implausible 44% lift that
       beat a real ADU on an actual test property - guarded via a lift cap.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from data_layer.models.database import (
    Base, Property, PropertyFeature, Neighborhood, PropertyType, ZoningType,
    User, UserRole,
)
from ml_layer.inference.improvement_recommender import ImprovementRecommender


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = Session(bind=engine)
    yield s
    s.close()

@pytest.fixture
def owner_id(session):
    u = User(email="sys@propiq.internal", full_name="sys",
              password_hash="!", role=UserRole.CLIENT, is_active=False)
    session.add(u); session.flush()
    return u.id


class FakeEngine:
    class _Result:
        def __init__(self, estimated_value):
            self.estimated_value = estimated_value

    def valuate(self, feature_dict):
        base = 500_000.0
        base += (feature_dict.get("building_sqft") or 0) * 200
        base += (feature_dict.get("bedrooms") or 0) * 15_000
        base += (feature_dict.get("bathrooms") or 0) * 10_000
        return self._Result(base)


@pytest.fixture
def engine():
    return ImprovementRecommender(FakeEngine())


def _property(
    owner_id, lot_size_sqft=14684, building_sqft=1307, bedrooms=4, bathrooms=3,
    property_type=PropertyType.SINGLE_FAMILY, pool=False, garage_spaces=2,
    adu_eligible=True, adu_max_sqft=653, far_ratio=None,
):
    p = Property(
        owner_id=owner_id,
        address="9205 Magnolia Dr", zip_code="92602", city="Irvine", state="CA",
        county="Orange", latitude=33.7, longitude=-117.7,
        bedrooms=bedrooms, bathrooms=bathrooms,
        building_sqft=building_sqft, lot_size_sqft=lot_size_sqft, year_built=1985,
        property_type=property_type, zoning=ZoningType.RESIDENTIAL_LOW,
        pool=pool, garage_spaces=garage_spaces,
        data_source="test",
    )
    p.features = PropertyFeature(
        adu_eligible=adu_eligible, adu_max_sqft=adu_max_sqft, far_ratio=far_ratio,
        development_score=60.0, walk_score=50, transit_score=40, school_rating=7,
    )
    p.neighborhood = Neighborhood(zip_code="92602", median_home_price=620_000)
    return p


def test_recommend_only_returns_feasible_with_lift(session, engine, owner_id):
    prop = _property(owner_id)
    session.add(prop); session.flush()
    recs = engine.recommend(prop)
    assert len(recs) > 0
    for r in recs:
        assert r.feasible is True
        assert r.value_lift_pct is not None


def test_value_lift_pct_is_plain_float_not_numpy(session, engine, owner_id):
    """Regression: engine.py used to leak np.float32 through _diff_value,
    which FastAPI/Pydantic serialization isn't guaranteed to handle."""
    prop = _property(owner_id)
    session.add(prop); session.flush()
    recs = engine.recommend(prop)
    assert len(recs) > 0
    for r in recs:
        if r.value_lift_pct is not None:
            assert type(r.value_lift_pct) is float, (
                f"{r.type}: value_lift_pct is {type(r.value_lift_pct)}, expected plain float"
            )


def test_adu_infeasible_when_not_eligible(session, engine, owner_id):
    prop = _property(owner_id, adu_eligible=False)
    session.add(prop); session.flush()
    recs = engine.recommend(prop)
    assert not any(r.type == "adu" for r in recs)


def test_pool_infeasible_when_already_present(session, engine, owner_id):
    prop = _property(owner_id, pool=True)
    session.add(prop); session.flush()
    recs = engine.recommend(prop)
    assert not any(r.type == "pool" for r in recs)


def test_garage_conversion_infeasible_with_no_garage(session, engine, owner_id):
    prop = _property(owner_id, garage_spaces=0)
    session.add(prop); session.flush()
    recs = engine.recommend(prop)
    assert not any(r.type == "garage_conversion" for r in recs)


def test_sb9_split_infeasible_on_small_lot(session, engine, owner_id):
    prop = _property(owner_id, lot_size_sqft=1800)  # below the 2400 sqft split threshold
    session.add(prop); session.flush()
    recs = engine.recommend(prop)
    assert not any(r.type == "sb9_split" for r in recs)


def test_sb9_split_lift_capped_below_adu(session, engine, owner_id):
    """Regression: SB9_LAND_VALUE_FRACTION at 0.35 (pre-fix) produced a 44%
    lift on this exact property, beating a real ADU (21.82%) - implausible,
    since a raw unentitled half-lot should be worth less than a livable
    accessory unit. Verified live against 9205 Magnolia Dr, Aug 2026."""
    prop = _property(owner_id)
    session.add(prop); session.flush()
    recs = {r.type: r for r in engine.recommend(prop)}
    if "sb9_split" in recs and "adu" in recs:
        assert recs["sb9_split"].value_lift_pct < recs["adu"].value_lift_pct, (
            "SB9 split lift should not exceed a real ADU's lift"
        )


def test_non_avm_methods_carry_a_caveat(session, engine, owner_id):
    """Anything not diffed through the AVM must be labeled as such - the
    frontend badges non-avm_diff cards, and an unlabeled rule-of-thumb
    number could be mistaken for a model prediction."""
    prop = _property(owner_id)
    session.add(prop); session.flush()
    for r in engine.recommend(prop):
        if r.method == "rule_of_thumb":
            assert r.caveat is not None and len(r.caveat) > 0


def test_recommendations_sorted_by_lift_descending(session, engine, owner_id):
    prop = _property(owner_id)
    session.add(prop); session.flush()
    recs = engine.recommend(prop)
    lifts = [r.value_lift_pct for r in recs]
    assert lifts == sorted(lifts, reverse=True)