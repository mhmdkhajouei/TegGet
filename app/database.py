"""
aiosqlite connection lifecycle and schema management.

Single shared connection for the whole process (SQLite + WAL mode handles
concurrent readers fine on a 1GB VPS; we avoid a connection pool to keep
memory flat). The connection is created in `init_db()` during the FastAPI
lifespan and closed in `close_db()`.
"""
import logging
import time

import aiosqlite

from app.config import settings

logger = logging.getLogger(__name__)

_CREATE_MARKET_SNAPSHOTS_SQL = """
CREATE TABLE IF NOT EXISTS market_snapshots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  INTEGER NOT NULL,
    price      REAL NOT NULL,
    volume     REAL,
    source     TEXT NOT NULL,
    change_24h REAL
);
"""

# Migration: add change_24h to existing deployments that have the
# market_snapshots table without this column. Same pattern as the
# last_triggered_at migration on alarms below — SQLite has no
# "ALTER TABLE ... ADD COLUMN IF NOT EXISTS", so we just try it and
# swallow the error when the column is already there.
_ALTER_MARKET_SNAPSHOTS_ADD_CHANGE_24H_SQL = """
ALTER TABLE market_snapshots ADD COLUMN change_24h REAL;
"""

_CREATE_TIMESTAMP_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_market_snapshots_timestamp
ON market_snapshots (timestamp);
"""

_CREATE_USERS_SQL = """
CREATE TABLE IF NOT EXISTS users (
    chat_id    INTEGER PRIMARY KEY,
    first_name TEXT,
    created_at INTEGER NOT NULL
);
"""

_CREATE_ALARMS_SQL = """
CREATE TABLE IF NOT EXISTS alarms (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id           INTEGER NOT NULL,
    target_price      REAL NOT NULL,
    condition         TEXT NOT NULL,
    frequency         TEXT NOT NULL,
    is_active         INTEGER NOT NULL DEFAULT 1,
    created_at        INTEGER NOT NULL,
    last_triggered_at INTEGER NOT NULL DEFAULT 0,
    is_armed          INTEGER NOT NULL DEFAULT 1
);
"""

# Migration: add last_triggered_at to existing deployments that have the
# alarms table without this column. SQLite does not support IF NOT EXISTS
# for ALTER TABLE ADD COLUMN, so we catch OperationalError silently.
_ALTER_ALARMS_ADD_LAST_TRIGGERED_SQL = """
ALTER TABLE alarms ADD COLUMN last_triggered_at INTEGER NOT NULL DEFAULT 0;
"""

_CREATE_ALARMS_CHAT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_alarms_chat_id_active
ON alarms (chat_id, is_active);
"""

_CREATE_NEWS_SUBSCRIPTIONS_SQL = """
CREATE TABLE IF NOT EXISTS news_subscriptions (
    chat_id INTEGER NOT NULL,
    source  TEXT NOT NULL,
    PRIMARY KEY (chat_id, source)
);
"""

_connection: aiosqlite.Connection | None = None


async def init_db() -> aiosqlite.Connection:
    """Open the shared connection, apply pragmas, and ensure schema exists."""
    global _connection

    if _connection is not None:
        return _connection

    conn = await aiosqlite.connect(settings.database_path)

    await conn.execute("PRAGMA journal_mode=WAL;")
    await conn.execute("PRAGMA synchronous=NORMAL;")
    await conn.execute("PRAGMA foreign_keys=ON;")

    await conn.execute(_CREATE_MARKET_SNAPSHOTS_SQL)
    await conn.execute(_CREATE_TIMESTAMP_INDEX_SQL)
    await conn.execute(_CREATE_USERS_SQL)
    await conn.execute(_CREATE_ALARMS_SQL)
    await conn.execute(_CREATE_ALARMS_CHAT_INDEX_SQL)
    await conn.execute(_CREATE_NEWS_SUBSCRIPTIONS_SQL)

    # Safe migration for existing databases that already have the alarms
    # table but are missing last_triggered_at (added in v1.0).
    try:
        await conn.execute(_ALTER_ALARMS_ADD_LAST_TRIGGERED_SQL)
        logger.info("Migration applied: added last_triggered_at to alarms")
    except Exception:
        # Column already exists — normal on any second startup.
        pass

    # Safe migration for existing databases that already have the
    # market_snapshots table but are missing change_24h (added when we
    # switched from a manual 15s diff to Wallex's official rolling
    # 24h change).
    try:
        await conn.execute(_ALTER_MARKET_SNAPSHOTS_ADD_CHANGE_24H_SQL)
        logger.info("Migration applied: added change_24h to market_snapshots")
    except Exception:
        # Column already exists — normal on any second startup.
        pass

    # Safe migration for existing databases: add is_armed to alarms table (added in v1.3 for Crossover logic)
    try:
        await conn.execute("ALTER TABLE alarms ADD COLUMN is_armed INTEGER NOT NULL DEFAULT 1;")
        logger.info("Migration applied: added is_armed to alarms")
    except Exception:
        # Column already exists — normal on subsequent startups.
        pass

    await conn.commit()

    _connection = conn
    logger.info("Database initialized at %s (WAL mode)", settings.database_path)
    return _connection


async def close_db() -> None:
    """Close the shared connection cleanly on shutdown."""
    global _connection
    if _connection is not None:
        await _connection.close()
        _connection = None
        logger.info("Database connection closed")


def get_db() -> aiosqlite.Connection:
    """Return the shared connection. Raises if init_db() hasn't run yet."""
    if _connection is None:
        raise RuntimeError(
            "Database not initialized. Call init_db() during app startup "
            "before using get_db()."
        )
    return _connection


async def insert_snapshot(
    timestamp: int,
    price: float,
    volume: float | None,
    source: str,
    change_24h: float | None = None,
) -> None:
    """
    Persist a single market snapshot.

    change_24h is the exchange's own rolling 24h percentage change (e.g.
    Wallex's `stats.24h_ch`), not something we compute ourselves. It's
    optional because not every source in the failover chain provides it
    (Bitpin/TetherLand don't) — those snapshots simply store NULL here.
    """
    conn = get_db()
    await conn.execute(
        "INSERT INTO market_snapshots (timestamp, price, volume, source, change_24h) "
        "VALUES (?, ?, ?, ?, ?)",
        (timestamp, price, volume, source, change_24h),
    )
    await conn.commit()


async def get_latest_snapshots(limit: int = 2) -> list[dict]:
    """
    Return the N most recent market snapshots (newest first).

    Returns an empty list if none exist yet. The evaluator in main.py uses
    its own in-memory previous price and does NOT call this function on
    every tick -- this is purely for the /price command.
    """
    conn = get_db()
    async with conn.execute(
        "SELECT timestamp, price, volume, source, change_24h FROM market_snapshots "
        "ORDER BY timestamp DESC LIMIT ?",
        (limit,),
    ) as cursor:
        rows = await cursor.fetchall()

    return [
        {
            "timestamp": r[0],
            "price": r[1],
            "volume": r[2],
            "source": r[3],
            "change_24h": r[4],
        }
        for r in rows
    ]


async def get_latest_snapshot() -> dict | None:
    """Return the single most recent snapshot, or None. Convenience wrapper."""
    results = await get_latest_snapshots(limit=1)
    return results[0] if results else None


async def upsert_user(chat_id: int, first_name: str | None) -> None:
    """
    Register a user on first /start, or refresh their first_name on
    subsequent /start calls. created_at is only set on first insert.
    """
    conn = get_db()
    await conn.execute(
        "INSERT INTO users (chat_id, first_name, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET first_name = excluded.first_name",
        (chat_id, first_name, int(time.time())),
    )
    await conn.commit()


async def count_active_alarms(chat_id: int) -> int:
    """Return how many active alarms this user currently has."""
    conn = get_db()
    async with conn.execute(
        "SELECT COUNT(*) FROM alarms WHERE chat_id = ? AND is_active = 1",
        (chat_id,),
    ) as cursor:
        row = await cursor.fetchone()
    return row[0] if row else 0


async def insert_alarm(
    chat_id: int, target_price: float, condition: str, frequency: str
) -> int:
    """Persist a new alarm and return its id."""
    conn = get_db()
    cursor = await conn.execute(
        "INSERT INTO alarms (chat_id, target_price, condition, frequency, is_active, created_at, last_triggered_at, is_armed) "
        "VALUES (?, ?, ?, ?, 1, ?, 0, 1)",
        (chat_id, target_price, condition, frequency, int(time.time())),
    )
    await conn.commit()
    return cursor.lastrowid


async def get_active_alarms() -> list[dict]:
    """
    Fetch all alarms where is_active = 1.
    Called by the trigger evaluator in main.py on every market poll cycle.
    """
    conn = get_db()
    async with conn.execute(
        "SELECT id, chat_id, target_price, condition, frequency, last_triggered_at, is_armed "
        "FROM alarms WHERE is_active = 1"
    ) as cursor:
        rows = await cursor.fetchall()

    return [
        {
            "id": r[0],
            "chat_id": r[1],
            "target_price": r[2],
            "condition": r[3],
            "frequency": r[4],
            "last_triggered_at": r[5],
            "is_armed": r[6],
        }
        for r in rows
    ]


async def deactivate_alarm(alarm_id: int) -> None:
    """Set is_active = 0 for a 'once' alarm after it fires."""
    conn = get_db()
    await conn.execute(
        "UPDATE alarms SET is_active = 0 WHERE id = ?",
        (alarm_id,),
    )
    await conn.commit()


async def update_alarm_triggered_at(alarm_id: int, triggered_at: int) -> None:
    """Record the last fire timestamp for 'daily' and 'every_time' alarms."""
    conn = get_db()
    await conn.execute(
        "UPDATE alarms SET last_triggered_at = ? WHERE id = ?",
        (triggered_at, alarm_id),
    )
    await conn.commit()


async def update_alarm_armed_status(alarm_id: int, is_armed: int) -> None:
    """Update the is_armed status (1 for true, 0 for false) to manage crossovers and hysteresis buffers."""
    conn = get_db()
    await conn.execute(
        "UPDATE alarms SET is_armed = ? WHERE id = ?",
        (is_armed, alarm_id),
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# News subscriptions (Profile & Management Hub, v1.1)
# ---------------------------------------------------------------------------

async def get_user_news_sources(chat_id: int) -> list[str]:
    """Return the list of RSS source names this chat_id is subscribed to."""
    conn = get_db()
    async with conn.execute(
        "SELECT source FROM news_subscriptions WHERE chat_id = ?",
        (chat_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [r[0] for r in rows]


async def toggle_news_source(chat_id: int, source: str) -> bool:
    """
    Toggle a single news source subscription for chat_id.

    Returns the new state: True if now subscribed, False if now
    unsubscribed. INSERT/DELETE + commit is done as a single connection
    round trip, so there's no race window between checking and acting.
    """
    conn = get_db()
    async with conn.execute(
        "SELECT 1 FROM news_subscriptions WHERE chat_id = ? AND source = ?",
        (chat_id, source),
    ) as cursor:
        row = await cursor.fetchone()

    if row is None:
        await conn.execute(
            "INSERT INTO news_subscriptions (chat_id, source) VALUES (?, ?)",
            (chat_id, source),
        )
        await conn.commit()
        return True
    else:
        await conn.execute(
            "DELETE FROM news_subscriptions WHERE chat_id = ? AND source = ?",
            (chat_id, source),
        )
        await conn.commit()
        return False


# ---------------------------------------------------------------------------
# Alarm management (Profile & Management Hub, v1.1)
# ---------------------------------------------------------------------------

async def get_user_alarms(chat_id: int) -> list[dict]:
    """
    Return all active and armed alarms for a chat_id, newest first.
    Used by the profile hub's alarm list and quota menu.
    """
    conn = get_db()
    async with conn.execute(
        "SELECT id, chat_id, target_price, condition, frequency, is_active, "
        "created_at, last_triggered_at, is_armed FROM alarms "
        "WHERE chat_id = ? AND is_active = 1 AND is_armed = 1 ORDER BY created_at DESC",
        (chat_id,),
    ) as cursor:
        rows = await cursor.fetchall()

    return [
        {
            "id": r[0],
            "chat_id": r[1],
            "target_price": r[2],
            "condition": r[3],
            "frequency": r[4],
            "is_active": bool(r[5]),
            "created_at": r[6],
            "last_triggered_at": r[7],
            "is_armed": r[8],
        }
        for r in rows
    ]


async def delete_alarm(alarm_id: int, chat_id: int) -> None:
    """
    Delete a specific alarm. chat_id is checked as part of the WHERE
    clause (not just alarm_id) so one user can never delete another
    user's alarm even if they somehow guess/forge an alarm_id in a
    callback payload.
    """
    conn = get_db()
    await conn.execute(
        "DELETE FROM alarms WHERE id = ? AND chat_id = ?",
        (alarm_id, chat_id),
    )
    await conn.commit()


async def update_alarm_target_price(alarm_id: int, chat_id: int, target_price: float) -> None:
    """
    Update an alarm's target price. chat_id-scoped for the same reason as
    delete_alarm.

    "Fresh Reset" on edit: any edit re-activates the alarm (is_active = 1),
    clears last_triggered_at (= 0), and rearms the state (is_armed = 1).
    Without this, editing a 'once' alarm that already fired-and-deactivated
    would silently stay dormant forever, and editing a 'daily' alarm
    would inherit a stale configuration. Editing an alarm is a strong
    enough signal of new intent that it should always re-enter the
    active evaluation loop immediately.
    """
    conn = get_db()
    await conn.execute(
        "UPDATE alarms SET target_price = ?, is_active = 1, last_triggered_at = 0, is_armed = 1 "
        "WHERE id = ? AND chat_id = ?",
        (target_price, alarm_id, chat_id),
    )
    await conn.commit()


async def update_alarm_condition(alarm_id: int, chat_id: int, condition: str) -> None:
    """
    Update an alarm's condition. chat_id-scoped for the same reason as
    delete_alarm. Same "Fresh Reset" as update_alarm_target_price — see
    that docstring for why is_active/last_triggered_at/is_armed are reset here too.
    """
    conn = get_db()
    await conn.execute(
        "UPDATE alarms SET condition = ?, is_active = 1, last_triggered_at = 0, is_armed = 1 "
        "WHERE id = ? AND chat_id = ?",
        (condition, alarm_id, chat_id),
    )
    await conn.commit()


async def update_alarm_frequency(alarm_id: int, chat_id: int, frequency: str) -> None:
    """
    Update an alarm's frequency. chat_id-scoped for the same reason as
    delete_alarm. Same "Fresh Reset" as update_alarm_target_price — see
    that docstring for why is_active/last_triggered_at are reset here too.
    This one matters especially for a switch to/from 'daily': an old
    last_triggered_at from before the edit would otherwise still gate the
    next fire under the new frequency's rules.
    """
    conn = get_db()
    await conn.execute(
        "UPDATE alarms SET frequency = ?, is_active = 1, last_triggered_at = 0 "
        "WHERE id = ? AND chat_id = ?",
        (frequency, alarm_id, chat_id),
    )
    await conn.commit()


async def get_alarm_by_id(alarm_id: int, chat_id: int) -> dict | None:
    """
    Fetch a single alarm scoped to chat_id. Used by the edit sub-flows to
    confirm the alarm still exists (and belongs to this user) before
    showing edit options or applying an update — handles the case where a
    user taps "edit" on an alarm that was deleted or already fired-and-
    deactivated in another session.
    """
    conn = get_db()
    async with conn.execute(
        "SELECT id, chat_id, target_price, condition, frequency, is_active, "
        "created_at, last_triggered_at, is_armed FROM alarms "
        "WHERE id = ? AND chat_id = ?",
        (alarm_id, chat_id),
    ) as cursor:
        row = await cursor.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "chat_id": row[1],
        "target_price": row[2],
        "condition": row[3],
        "frequency": row[4],
        "is_active": bool(row[5]),
        "created_at": row[6],
        "last_triggered_at": row[7],
        "is_armed": row[8],
    }