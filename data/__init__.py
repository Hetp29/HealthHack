"""GlucoTwin canonical data layer (contract v1.0). Owner: Het Patel.

Typical consumer usage:

    from data.repository import DataRepository
    repo = DataRepository()            # $GLUCOTWIN_DB_URL or sqlite:///data/glucotwin.db
    repo.list_participants()

See data/README.md for commands and the full interface.
"""

from data.schema import SCHEMA_VERSION

__all__ = ["SCHEMA_VERSION"]
