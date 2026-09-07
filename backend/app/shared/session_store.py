"""Per-session Chroma stores for user uploads, kept apart from the corpus.

The persistent corpus and a user's ad-hoc uploads live in *separate* Chroma
directories rather than in one collection separated by ``corpus_scope``
metadata. The reason is lifecycle, not retrieval: session uploads are
ephemeral, are dropped wholesale when the session ends or expires, and must
never be able to reach the corpus that RQ1 and RQ2 are measured against. A
directory per session makes "delete everything this user uploaded" an
``rmtree`` that cannot half-succeed, and makes contamination of the corpus
structurally impossible rather than dependent on every query remembering to
pass a filter.

Chunks still carry ``corpus_scope`` (``session:{id}``) so a passage remains
self-describing once it has been retrieved and is being cited.

BM25 term statistics are per store, which is a second benefit: with one shared
index, scope filtering happens after scoring, so uploads shift the IDF used to
rank corpus results. Separate stores remove that coupling.
"""

from __future__ import annotations

import logging
import re
import shutil
import time
import uuid
from pathlib import Path

from app.core.config import settings
from app.shared.keyword_index import KeywordIndex
from app.shared.vector_store import VectorStore

logger = logging.getLogger(__name__)

# A session id becomes a directory name, so it is whitelisted rather than
# escaped: anything outside this set is rejected. Without this a crafted id
# ("../../chroma") would let an upload write into, or a delete remove, the
# corpus store.
_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# What the API accepts from a client. Stricter than _SAFE_SESSION_ID because a
# session id is a bearer capability: there is no authentication, so anyone who
# can guess an id can upload into that session or delete it. Ids are therefore
# issued by :func:`new_session_id` and must look like what it produces.
_ISSUED_SESSION_ID = re.compile(r"^[0-9a-f]{32}$")

# Directories awaiting deletion, renamed out of the way so a session is gone
# from the caller's view even if removal only partly succeeds.
_DELETED_PREFIX = ".deleted-"


class InvalidSessionId(ValueError):
    """The session id is not usable as a directory name."""


# Opened stores, keyed by session id. Chroma clients are relatively expensive
# to construct, and a session makes several requests.
_stores: dict[str, tuple[VectorStore, KeywordIndex]] = {}


def new_session_id() -> str:
    """Issue an unguessable session id."""
    return uuid.uuid4().hex


def validate_issued_session_id(session_id: str) -> str:
    """Check an id supplied by a client, which must be one we issued.

    Format alone cannot prove provenance, but requiring the shape of a UUID4
    hex string makes an id infeasible to guess, which is the property that
    matters when the id is the only thing protecting a session's uploads.
    """
    if not isinstance(session_id, str) or not _ISSUED_SESSION_ID.match(session_id):
        raise InvalidSessionId(
            "session id must be a 32-character hexadecimal string issued by "
            "POST /api/session"
        )
    return session_id


def scope_for(session_id: str) -> str:
    """The ``corpus_scope`` value carried by this session's chunks."""
    return f"session:{validate_session_id(session_id)}"


def validate_session_id(session_id: str) -> str:
    if not isinstance(session_id, str) or not _SAFE_SESSION_ID.match(session_id):
        raise InvalidSessionId(
            "session id must be 1-64 characters of letters, digits, hyphen or "
            "underscore, starting with a letter or digit"
        )
    return session_id


def session_root() -> Path:
    return Path(settings.SESSION_STORE_ROOT)


def session_path(session_id: str) -> Path:
    root = session_root().resolve()
    path = (root / validate_session_id(session_id)).resolve()
    # Belt and braces. The whitelist above already makes escape impossible;
    # this keeps that true if the pattern is ever loosened.
    if not path.is_relative_to(root):
        raise InvalidSessionId("session id resolves outside the session root")
    return path


def get_session_stores(session_id: str) -> tuple[VectorStore, KeywordIndex]:
    """The vector store and keyword index for one session, opened on first use."""
    session_id = validate_session_id(session_id)

    cached = _stores.get(session_id)
    if cached is not None:
        return cached

    path = session_path(session_id)
    path.mkdir(parents=True, exist_ok=True)
    store = VectorStore(path=str(path))
    stores = (store, KeywordIndex.rebuild_from(store))
    _stores[session_id] = stores
    return stores


def drop_session(session_id: str) -> bool:
    """Delete a session's store entirely. Returns whether anything was removed.

    Any open client is closed first: Chroma caches a system per path, so on
    Windows the SQLite file stays open and the directory cannot be removed.
    """
    session_id = validate_session_id(session_id)

    cached = _stores.pop(session_id, None)
    if cached is not None:
        cached[0].close()

    path = session_path(session_id)
    if not path.exists():
        return False

    # Rename first, then delete. A rename within one filesystem is atomic, so
    # the session is gone from every caller's view the moment it succeeds. A
    # plain rmtree that fails halfway would instead leave a half-deleted Chroma
    # directory that still opens and answers queries with whatever survived.
    grave = path.parent / f"{_DELETED_PREFIX}{uuid.uuid4().hex}"
    try:
        path.rename(grave)
    except OSError as exc:
        logger.warning("could not rename session store %s for deletion: %s", session_id, exc)
        return False

    shutil.rmtree(grave, ignore_errors=True)
    if grave.exists():
        # Debris, not a live session. purge_expired sweeps it later.
        logger.warning("session %s renamed but not fully removed", session_id)

    logger.info("dropped session store %s", session_id)
    return True


def purge_expired(max_age_hours: float | None = None) -> list[str]:
    """Delete session stores older than the TTL. Returns the ids removed.

    Ephemeral means ephemeral: without a sweep, an abandoned session's uploads
    would sit on disk indefinitely, which is the data-retention problem that
    separating the stores was meant to avoid.
    """
    ttl = settings.SESSION_TTL_HOURS if max_age_hours is None else max_age_hours
    root = session_root()
    if not root.exists():
        return []

    cutoff = time.time() - ttl * 3600
    removed: list[str] = []

    for path in root.iterdir():
        if not path.is_dir():
            continue

        if path.name.startswith(_DELETED_PREFIX):
            # Debris from a deletion that did not finish. Always collectable.
            shutil.rmtree(path, ignore_errors=True)
            continue

        if path.stat().st_mtime >= cutoff:
            continue
        try:
            if drop_session(path.name):
                removed.append(path.name)
        except InvalidSessionId:
            # A directory nobody here created. Left alone deliberately.
            logger.warning("skipping unrecognised session directory: %s", path.name)

    return removed


def reset_cache() -> None:
    """Forget opened stores without deleting their data (tests).

    Each client is closed on the way out. Chroma caches a system per path, so
    a merely forgotten client keeps its SQLite file open and the directory
    undeletable afterwards.
    """
    for store, _ in _stores.values():
        store.close()
    _stores.clear()
