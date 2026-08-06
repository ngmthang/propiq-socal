"""
    add real list_price column

    Property.list_price was previously a hybrid_property that just mirrored
    estimated_value (the AVM prediction) - there was no column anywhere
    actually holding a real, market-observed listing price. Real listing
    ingestion (Redfin) now populates one, so it needs a real place to live,
    distinct from the model's own estimate.

    Revision ID: a1c9f3d2e7b4
    Revises: 7ea4c4dc5fca
    Create Date: 2026-08-06 00:00:00.000000

    @author Minh Thang Nguyen
    @version August 5, 2026
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c9f3d2e7b4'
down_revision: Union[str, None] = '7ea4c4dc5fca'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('properties', sa.Column('list_price', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('properties', 'list_price')