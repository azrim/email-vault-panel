"""SQLite vault for catch-all aliases."""
from __future__ import annotations

import csv
import secrets
import sqlite3
import string
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

STATUSES = frozenset({"fresh", "archived", "reserved", "used", "burned", "cooldown"})


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rand_token(n: int = 7) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def make_local(style: str, prefix: str, seq: int) -> str:
    prefix = (prefix or "x").strip().rstrip("_") or "x"
    if style == "seq":
        return f"{prefix}{seq:03d}_{rand_token(3)}"
    if style == "word":
        c, v = "bcdfghjkmnpqrstvwxyz", "aeiou"
        core = "".join(secrets.choice(c) + secrets.choice(v) for _ in range(3))
        return f"{prefix}_{core}{rand_token(2)}"
    return f"{prefix}_{rand_token(7)}"


class Vault:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    status TEXT NOT NULL DEFAULT 'fresh',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_aliases_status ON aliases(status)"
            )

    def list_aliases(
        self,
        *,
        q: str = "",
        status: str | None = None,
        include_archived: bool = False,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        elif not include_archived:
            clauses.append("status != 'archived'")
        if q.strip():
            clauses.append("(email LIKE ? OR notes LIKE ?)")
            like = f"%{q.strip()}%"
            params.extend([like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        sql = f"""
            SELECT id, email, status, notes, created_at, updated_at
            FROM aliases
            {where}
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT ? OFFSET ?
        """
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def counts(self) -> dict[str, int]:
        with self.connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM aliases").fetchone()[0]
            by = conn.execute(
                "SELECT status, COUNT(*) AS c FROM aliases GROUP BY status"
            ).fetchall()
        out = {"total": int(total)}
        for r in by:
            out[str(r["status"])] = int(r["c"])
        return out

    def get(self, alias_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, email, status, notes, created_at, updated_at FROM aliases WHERE id = ?",
                (alias_id,),
            ).fetchone()
        return dict(row) if row else None

    def generate(
        self,
        *,
        count: int,
        domain: str,
        prefix: str = "x",
        style: str = "random",
        notes: str = "",
        status: str = "fresh",
    ) -> list[dict[str, Any]]:
        if count < 1 or count > 500:
            raise ValueError("count must be 1..500")
        if status not in STATUSES:
            raise ValueError(f"invalid status {status}")
        domain = domain.strip().lower().lstrip("@")
        if not domain or "." not in domain:
            raise ValueError("invalid domain")
        with self.connect() as conn:
            existing = {
                r[0].lower()
                for r in conn.execute("SELECT email FROM aliases").fetchall()
            }
            now = utc_now()
            created: list[dict[str, Any]] = []
            guard = 0
            while len(created) < count and guard < count * 30:
                guard += 1
                local = make_local(style, prefix, len(created) + 1)
                email = f"{local}@{domain}".lower()
                if email in existing:
                    continue
                existing.add(email)
                cur = conn.execute(
                    """
                    INSERT INTO aliases (email, status, notes, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (email, status, notes, now, now),
                )
                created.append(
                    {
                        "id": cur.lastrowid,
                        "email": email,
                        "status": status,
                        "notes": notes,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            if len(created) < count:
                raise RuntimeError(f"only generated {len(created)} unique addresses")
        return created

    def patch(
        self,
        alias_id: int,
        *,
        status: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any] | None:
        row = self.get(alias_id)
        if not row:
            return None
        new_status = status if status is not None else row["status"]
        new_notes = notes if notes is not None else row["notes"]
        if new_status not in STATUSES:
            raise ValueError(f"invalid status {new_status}")
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "UPDATE aliases SET status = ?, notes = ?, updated_at = ? WHERE id = ?",
                (new_status, new_notes, now, alias_id),
            )
        return self.get(alias_id)

    def archive(self, alias_id: int) -> dict[str, Any] | None:
        return self.patch(alias_id, status="archived")

    def unarchive(self, alias_id: int) -> dict[str, Any] | None:
        return self.patch(alias_id, status="fresh")

    def import_csv(self, path: Path) -> dict[str, int]:
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(str(path))
        added = skipped = 0
        now = utc_now()
        with path.open(newline="", encoding="utf-8-sig") as f, self.connect() as conn:
            reader = csv.DictReader(f)
            for raw in reader:
                email = (raw.get("email") or "").strip().lower()
                if not email or "@" not in email:
                    continue
                status = (raw.get("status") or "fresh").strip().lower()
                if status not in STATUSES:
                    status = "fresh"
                notes = (raw.get("notes") or "").strip()
                created = (raw.get("created_at") or now).strip() or now
                try:
                    conn.execute(
                        """
                        INSERT INTO aliases (email, status, notes, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (email, status, notes, created, now),
                    )
                    added += 1
                except sqlite3.IntegrityError:
                    skipped += 1
        return {"added": added, "skipped": skipped}
