"""device_connections.status gains 'demo_simulated'

A status for labelled sample sources in a demo account, so that `authorized` and
`connected` never have to stand in for "simulated". Backward compatible: existing
rows keep their status and no existing value changes meaning.

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

_OLD = "status IN ('pending','authorized','connected','error','revoked','disconnected')"
_NEW = ("status IN ('pending','authorized','connected','error','revoked','disconnected',"
        "'demo_simulated')")


def upgrade() -> None:
    with op.batch_alter_table('device_connections', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_device_connections_status'), type_='check')
        batch_op.create_check_constraint('status', _NEW)


def downgrade() -> None:
    # Simulated sources cannot exist under the old constraint; they hold no real data.
    op.execute("DELETE FROM device_connections WHERE status = 'demo_simulated'")
    with op.batch_alter_table('device_connections', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_device_connections_status'), type_='check')
        batch_op.create_check_constraint('status', _OLD)
