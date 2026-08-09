"""Token-revocation persistence (session revocation on /auth/logout).

`jti` is `revoked_token`'s PRIMARY KEY, so `is_revoked` - run on every
authenticated request via AuthService.current_user_from_access - is a single
indexed lookup, not a table scan; the index is free, already scaffolded in
migration 0001. `revoke` is idempotent (ON CONFLICT DO NOTHING) so calling
/auth/logout twice with the same token, or logging out an access and refresh
token that happen to collide, is harmless.

# ponytail: nothing purges rows past their expires_at, so the table grows
# unboundedly with every logout. The PK index keeps is_revoked() fast
# regardless of row count, so this is a storage-growth concern, not a
# latency one - add a periodic `DELETE WHERE expires_at < now()` if that
# ever matters.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

import psycopg


class RevokedTokenRepository:
    def __init__(self, cur: psycopg.Cursor) -> None:
        self.cur = cur

    def revoke(self, *, jti: str, user_id: UUID | str, expires_at: datetime) -> None:
        self.cur.execute(
            """
            INSERT INTO revoked_token (jti, user_id, expires_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (jti) DO NOTHING
            """,
            (jti, str(user_id), expires_at),
        )

    def is_revoked(self, jti: str) -> bool:
        self.cur.execute("SELECT 1 FROM revoked_token WHERE jti = %s", (jti,))
        return self.cur.fetchone() is not None
