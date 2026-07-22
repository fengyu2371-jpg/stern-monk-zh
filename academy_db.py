from __future__ import annotations

import calendar
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WeekInfo:
    key: str
    label: str
    year: int
    month: int
    week_index: int
    start_date: date
    end_date: date


def month_week_info(target: date | None = None) -> WeekInfo:
    current = target or date.today()
    week_index = ((current.day - 1) // 7) + 1
    start_day = ((week_index - 1) * 7) + 1
    last_day = calendar.monthrange(current.year, current.month)[1]
    end_day = min(week_index * 7, last_day)

    return WeekInfo(
        key=f"{current.year:04d}-{current.month:02d}-{week_index}",
        label=f"{current.month}-{week_index}",
        year=current.year,
        month=current.month,
        week_index=week_index,
        start_date=date(current.year, current.month, start_day),
        end_date=date(current.year, current.month, end_day),
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AcademyDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 15000;")
        return conn

    def _migrate_oracle_pages_for_unlimited_draws(
        self,
        conn: sqlite3.Connection,
    ) -> None:
        row = conn.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'table' AND name = 'oracle_pages'
            """
        ).fetchone()
        if row is None:
            return

        normalized_sql = "".join(str(row["sql"] or "").upper().split())
        if "UNIQUE(USER_ID,WEEK_KEY)" not in normalized_sql:
            return

        # v12 以前每位玩家每週只能有一頁。
        # 改建資料表並完整保留既有神諭。
        conn.executescript(
            """
            ALTER TABLE oracle_pages
            RENAME TO oracle_pages_limited_backup;

            DROP INDEX IF EXISTS idx_oracle_pages_user_week;

            CREATE TABLE oracle_pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                week_key TEXT NOT NULL,
                week_label TEXT NOT NULL,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                week_index INTEGER NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                oracle_text TEXT NOT NULL,
                used_keywords TEXT NOT NULL DEFAULT '',
                used_place_names TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '未完成',
                completed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id)
                    REFERENCES student_profiles(user_id)
                    ON DELETE CASCADE
            );

            INSERT INTO oracle_pages (
                id, user_id, week_key, week_label, year, month,
                week_index, period_start, period_end, oracle_text,
                used_keywords, used_place_names, status, completed_at,
                created_at, updated_at
            )
            SELECT
                id, user_id, week_key, week_label, year, month,
                week_index, period_start, period_end, oracle_text,
                used_keywords, used_place_names, status, completed_at,
                created_at, updated_at
            FROM oracle_pages_limited_backup;

            DROP TABLE oracle_pages_limited_backup;

            CREATE INDEX IF NOT EXISTS idx_oracle_pages_user_week
            ON oracle_pages(user_id, year, month, week_index);
            """
        )

    def initialize(self) -> None:
        with closing(self.connect()) as conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS student_profiles (
                    user_id TEXT PRIMARY KEY,
                    student_name TEXT NOT NULL,
                    preferred_name TEXT NOT NULL,
                    house TEXT NOT NULL,
                    major TEXT NOT NULL DEFAULT '',
                    enrollment_year TEXT NOT NULL DEFAULT '',
                    introduction TEXT NOT NULL DEFAULT '',
                    companion_name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS oracle_preferences (
                    user_id TEXT PRIMARY KEY,
                    liked_themes TEXT NOT NULL DEFAULT '',
                    avoided_topics TEXT NOT NULL DEFAULT '',
                    creative_keywords TEXT NOT NULL DEFAULT '',
                    preferred_scenes TEXT NOT NULL DEFAULT '',
                    allow_place_context INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id)
                        REFERENCES student_profiles(user_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS student_places (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    place_type TEXT NOT NULL,
                    district TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    source_kind TEXT NOT NULL DEFAULT '新登記',
                    status TEXT NOT NULL DEFAULT '使用中',
                    allow_oracle INTEGER NOT NULL DEFAULT 1,
                    is_public INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id)
                        REFERENCES student_profiles(user_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_student_places_user
                ON student_places(user_id);

                CREATE INDEX IF NOT EXISTS idx_student_places_public
                ON student_places(is_public, place_type);

                CREATE TABLE IF NOT EXISTS oracle_pages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    week_key TEXT NOT NULL,
                    week_label TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    week_index INTEGER NOT NULL,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    oracle_text TEXT NOT NULL,
                    used_keywords TEXT NOT NULL DEFAULT '',
                    used_place_names TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '未完成',
                    completed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id)
                        REFERENCES student_profiles(user_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_oracle_pages_user_week
                ON oracle_pages(user_id, year, month, week_index);
                """
            )
            self._migrate_oracle_pages_for_unlimited_draws(conn)

            # v12：所有學生自建地點都能作為該玩家的神諭素材。
            # 保留 allow_oracle 欄位以相容舊資料，但值統一為 1。
            conn.execute(
                "UPDATE student_places SET allow_oracle = 1 "
                "WHERE allow_oracle <> 1"
            )
            conn.commit()

    @staticmethod
    def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def save_profile(
        self,
        *,
        user_id: int,
        student_name: str,
        preferred_name: str,
        house: str,
        major: str,
        enrollment_year: str,
        introduction: str,
        companion_name: str,
    ) -> None:
        now = utc_now_iso()
        with closing(self.connect()) as conn:
            conn.execute(
                """
                INSERT INTO student_profiles (
                    user_id, student_name, preferred_name, house, major,
                    enrollment_year, introduction, companion_name,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    student_name = excluded.student_name,
                    preferred_name = excluded.preferred_name,
                    house = excluded.house,
                    major = excluded.major,
                    enrollment_year = excluded.enrollment_year,
                    introduction = excluded.introduction,
                    companion_name = excluded.companion_name,
                    updated_at = excluded.updated_at
                """,
                (
                    str(user_id),
                    student_name.strip(),
                    preferred_name.strip(),
                    house.strip(),
                    major.strip(),
                    enrollment_year.strip(),
                    introduction.strip(),
                    companion_name.strip(),
                    now,
                    now,
                ),
            )
            conn.commit()

    def save_preferences(
        self,
        *,
        user_id: int,
        liked_themes: str,
        avoided_topics: str,
        creative_keywords: str,
        preferred_scenes: str,
        allow_place_context: bool,
    ) -> None:
        now = utc_now_iso()
        with closing(self.connect()) as conn:
            conn.execute(
                """
                INSERT INTO oracle_preferences (
                    user_id, liked_themes, avoided_topics, creative_keywords,
                    preferred_scenes, allow_place_context, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    liked_themes = excluded.liked_themes,
                    avoided_topics = excluded.avoided_topics,
                    creative_keywords = excluded.creative_keywords,
                    preferred_scenes = excluded.preferred_scenes,
                    allow_place_context = excluded.allow_place_context,
                    updated_at = excluded.updated_at
                """,
                (
                    str(user_id),
                    liked_themes.strip(),
                    avoided_topics.strip(),
                    creative_keywords.strip(),
                    preferred_scenes.strip(),
                    int(bool(allow_place_context)),
                    now,
                ),
            )
            conn.commit()

    def get_profile(self, user_id: int) -> dict[str, Any] | None:
        with closing(self.connect()) as conn:
            row = conn.execute(
                "SELECT * FROM student_profiles WHERE user_id = ?",
                (str(user_id),),
            ).fetchone()
        return self._row_dict(row)

    def get_preferences(self, user_id: int) -> dict[str, Any] | None:
        with closing(self.connect()) as conn:
            row = conn.execute(
                "SELECT * FROM oracle_preferences WHERE user_id = ?",
                (str(user_id),),
            ).fetchone()
        return self._row_dict(row)

    def get_profile_bundle(self, user_id: int) -> dict[str, Any] | None:
        profile = self.get_profile(user_id)
        if profile is None:
            return None
        profile["preferences"] = self.get_preferences(user_id) or {
            "liked_themes": "",
            "avoided_topics": "",
            "creative_keywords": "",
            "preferred_scenes": "",
            "allow_place_context": 1,
        }
        return profile

    def delete_profile(self, user_id: int) -> bool:
        with closing(self.connect()) as conn:
            cursor = conn.execute(
                "DELETE FROM student_profiles WHERE user_id = ?",
                (str(user_id),),
            )
            conn.commit()
        return cursor.rowcount > 0

    def create_place(
        self,
        *,
        user_id: int,
        name: str,
        place_type: str,
        district: str,
        description: str,
        source_kind: str,
        status: str,
        allow_oracle: bool,
        is_public: bool,
    ) -> int:
        now = utc_now_iso()
        with closing(self.connect()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO student_places (
                    user_id, name, place_type, district, description,
                    source_kind, status, allow_oracle, is_public,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(user_id),
                    name.strip(),
                    place_type.strip(),
                    district.strip(),
                    description.strip(),
                    source_kind.strip(),
                    status.strip(),
                    1,
                    int(bool(is_public)),
                    now,
                    now,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def get_user_place(
        self,
        *,
        user_id: int,
        place_id: int,
    ) -> dict[str, Any] | None:
        with closing(self.connect()) as conn:
            row = conn.execute(
                """
                SELECT * FROM student_places
                WHERE id = ? AND user_id = ?
                """,
                (int(place_id), str(user_id)),
            ).fetchone()
        return self._row_dict(row)

    def update_place_visibility(
        self,
        *,
        user_id: int,
        place_id: int,
        is_public: bool,
    ) -> dict[str, Any] | None:
        now = utc_now_iso()
        with closing(self.connect()) as conn:
            cursor = conn.execute(
                """
                UPDATE student_places
                SET is_public = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    int(bool(is_public)),
                    now,
                    int(place_id),
                    str(user_id),
                ),
            )
            conn.commit()

        if cursor.rowcount <= 0:
            return None
        return self.get_user_place(
            user_id=user_id,
            place_id=place_id,
        )

    def list_user_places(self, user_id: int) -> list[dict[str, Any]]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM student_places
                WHERE user_id = ?
                ORDER BY id ASC
                """,
                (str(user_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_public_places(
        self,
        place_type: str | None = None,
    ) -> list[dict[str, Any]]:
        with closing(self.connect()) as conn:
            if place_type:
                rows = conn.execute(
                    """
                    SELECT p.*, s.preferred_name AS owner_name
                    FROM student_places AS p
                    JOIN student_profiles AS s ON s.user_id = p.user_id
                    WHERE p.is_public = 1 AND p.place_type = ?
                    ORDER BY p.id ASC
                    """,
                    (place_type,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT p.*, s.preferred_name AS owner_name
                    FROM student_places AS p
                    JOIN student_profiles AS s ON s.user_id = p.user_id
                    WHERE p.is_public = 1
                    ORDER BY p.id ASC
                    """
                ).fetchall()
        return [dict(row) for row in rows]

    def list_oracle_places(self, user_id: int) -> list[dict[str, Any]]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM student_places
                WHERE user_id = ?
                ORDER BY id ASC
                """,
                (str(user_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_oracle_by_week(
        self,
        user_id: int,
        week_key: str,
    ) -> dict[str, Any] | None:
        with closing(self.connect()) as conn:
            row = conn.execute(
                """
                SELECT * FROM oracle_pages
                WHERE user_id = ? AND week_key = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (str(user_id), week_key),
            ).fetchone()
        return self._row_dict(row)

    def count_oracles_by_week(
        self,
        user_id: int,
        week_key: str,
    ) -> int:
        with closing(self.connect()) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM oracle_pages
                WHERE user_id = ? AND week_key = ?
                """,
                (str(user_id), week_key),
            ).fetchone()
        return int(row["total"] if row is not None else 0)

    def create_oracle(
        self,
        *,
        user_id: int,
        week: WeekInfo,
        oracle_text: str,
        used_keywords: str,
        used_place_names: str,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with closing(self.connect()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO oracle_pages (
                    user_id, week_key, week_label, year, month, week_index,
                    period_start, period_end, oracle_text, used_keywords,
                    used_place_names, status, completed_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '未完成', NULL, ?, ?)
                """,
                (
                    str(user_id),
                    week.key,
                    week.label,
                    week.year,
                    week.month,
                    week.week_index,
                    week.start_date.isoformat(),
                    week.end_date.isoformat(),
                    oracle_text.strip(),
                    used_keywords.strip(),
                    used_place_names.strip(),
                    now,
                    now,
                ),
            )
            page_id = int(cursor.lastrowid)
            conn.commit()

        page = self.get_oracle(page_id)
        if page is None:
            raise RuntimeError("神諭頁面建立失敗。")
        return page

    def delete_oracle(
        self,
        *,
        page_id: int,
        user_id: int,
    ) -> bool:
        with closing(self.connect()) as conn:
            cursor = conn.execute(
                """
                DELETE FROM oracle_pages
                WHERE id = ? AND user_id = ?
                """,
                (int(page_id), str(user_id)),
            )
            conn.commit()
        return cursor.rowcount > 0


    def list_oracles(self, user_id: int) -> list[dict[str, Any]]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM oracle_pages
                WHERE user_id = ?
                ORDER BY year ASC, month ASC, week_index ASC, id ASC
                """,
                (str(user_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_oracle(self, page_id: int) -> dict[str, Any] | None:
        with closing(self.connect()) as conn:
            row = conn.execute(
                "SELECT * FROM oracle_pages WHERE id = ?",
                (int(page_id),),
            ).fetchone()
        return self._row_dict(row)

    def set_oracle_status(
        self,
        *,
        page_id: int,
        user_id: int,
        status: str,
    ) -> dict[str, Any] | None:
        if status not in {"已完成", "未完成"}:
            raise ValueError("神諭狀態只能是已完成或未完成。")

        now = utc_now_iso()
        completed_at = now if status == "已完成" else None

        with closing(self.connect()) as conn:
            conn.execute(
                """
                UPDATE oracle_pages
                SET status = ?, completed_at = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (status, completed_at, now, int(page_id), str(user_id)),
            )
            conn.commit()

        page = self.get_oracle(page_id)
        if page is None or page["user_id"] != str(user_id):
            return None
        return page
