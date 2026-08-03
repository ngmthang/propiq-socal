"""
    Regression tests for oc_parcel ingestion upsert logic.

    The headline test guards the bug that silently dropped ~28,600 real
    parcels county-wide: upsert_parcel used to skip any parcel with a blank
    SITE_ADDRESS, but the county legitimately leaves addresses blank on many
    valid parcels (dense new construction). A blank address is missing data,
    not an invalid parcel - it must be stored, not skipped.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from data_layer.models.database import Base, Property, User, UserRole, PropertyType, ZoningType
from data_layer.scrapers.ingest_oc_parcels import upsert_parcel


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


def _parcel(apn="527-181-99", address="1 REAL ST", zip_code="92602"):
    return {
        "parcel_number": apn, "source_id": apn, "source": "oc_parcel_gis",
        "address": address, "city": "Irvine", "state": "CA", "zip_code": zip_code,
        "county": "Orange", "latitude": 33.74, "longitude": -117.77,
        "year_built": 2015, "bedrooms": 3, "units": 1, "zoning": None,
        "property_type": None, "source_url": "", "raw_data": {},
    }


def test_blank_address_parcel_is_created_not_skipped(session, owner_id):
    """THE regression: a valid-APN parcel with a blank address must be stored."""
    result = upsert_parcel(session, _parcel(address=""), owner_id)
    session.flush()
    assert result == "created", f"blank-address parcel was {result}, expected 'created'"
    stored = session.query(Property).filter_by(parcel_number="527-181-99").one()
    assert stored.address == ""            # stored as empty string, not skipped
    assert stored.zip_code == "92602"


def test_missing_apn_is_skipped(session, owner_id):
    """A parcel with no APN genuinely can't be keyed - correct to skip."""
    assert upsert_parcel(session, _parcel(apn=None), owner_id) == "skipped"


def test_missing_zip_is_skipped(session, owner_id):
    """Zip is the one field we truly need - correct to skip without it."""
    assert upsert_parcel(session, _parcel(zip_code=None), owner_id) == "skipped"


def test_normal_parcel_is_created(session, owner_id):
    assert upsert_parcel(session, _parcel(), owner_id) == "created"


def test_reingest_updates_not_duplicates(session, owner_id):
    """Same APN twice -> one row, second is an update (idempotent ingest)."""
    assert upsert_parcel(session, _parcel(), owner_id) == "created"
    session.flush()
    assert upsert_parcel(session, _parcel(address="2 UPDATED AVE"), owner_id) == "updated"
    session.flush()
    assert session.query(Property).filter_by(parcel_number="527-181-99").count() == 1


def test_reingest_blank_does_not_clobber_real_address(session, owner_id):
    """A later blank-address fetch must not erase a real stored address."""
    upsert_parcel(session, _parcel(address="1 REAL ST"), owner_id); session.flush()
    upsert_parcel(session, _parcel(address=""), owner_id); session.flush()
    stored = session.query(Property).filter_by(parcel_number="527-181-99").one()
    assert stored.address == "1 REAL ST", "blank re-ingest clobbered a real address"