"""Inter-process lock used by every JSON Index v2 state transition."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def project_index_lock(project_root: Path, *, timeout_s: float = 30.0) -> Iterator[None]:
    # Lazy import avoids making db.storage -> json_index -> jobs.store -> db.storage a
    # module-import cycle. Storage invokes this only after its own module is initialized.
    from services.jobs.store import interprocess_lock

    lock_path = project_root / "indexes" / ".index.lock"
    with interprocess_lock(lock_path, timeout_s=timeout_s) as acquired:
        if not acquired:
            raise TimeoutError(f"Timed out locking JSON Index v2: {lock_path}")
        yield


@contextmanager
def project_rebuild_lock(project_root: Path) -> Iterator[None]:
    """Serialize expensive full scans while leaving source mutations unblocked."""

    from services.jobs.store import interprocess_lock

    lock_path = project_root / "indexes" / ".rebuild.lock"
    timeout_s = float(os.environ.get("GEOTILE_JSON_INDEX_REBUILD_LOCK_TIMEOUT_S", "1800"))
    with interprocess_lock(lock_path, timeout_s=max(1.0, timeout_s)) as acquired:
        if not acquired:
            raise TimeoutError(f"Timed out waiting for JSON Index v2 rebuild: {lock_path}")
        yield
