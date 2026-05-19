from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
EVENTS_DIR = PLUGIN_ROOT / "data" / "cthulhu"


def up(cursor):
    cursor.executescript(
        """
        CREATE TABLE IF NOT EXISTS true_names (
            name_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name_string TEXT NOT NULL UNIQUE,
            god_type TEXT NOT NULL,
            tier TEXT NOT NULL,
            threshold INTEGER NOT NULL,
            progress INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            owner_user_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            called_at TEXT,
            consumed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_true_names_status_god_tier
            ON true_names(status, god_type, tier);
        CREATE INDEX IF NOT EXISTS idx_true_names_owner
            ON true_names(owner_user_id, status);

        CREATE TABLE IF NOT EXISTS true_name_votes (
            vote_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name_id INTEGER NOT NULL,
            voter_user_id TEXT NOT NULL,
            voted_at TEXT NOT NULL,
            FOREIGN KEY (name_id) REFERENCES true_names(name_id)
        );
        CREATE INDEX IF NOT EXISTS idx_true_name_votes_name ON true_name_votes(name_id);
        CREATE INDEX IF NOT EXISTS idx_true_name_votes_voter ON true_name_votes(voter_user_id, voted_at);

        CREATE TABLE IF NOT EXISTS cthulhu_authority (
            authority_id TEXT PRIMARY KEY,
            god_type TEXT NOT NULL,
            tier TEXT NOT NULL,
            current_holder TEXT,
            acquired_at TEXT,
            previous_holder TEXT,
            previous_acquired_at TEXT
        );

        CREATE TABLE IF NOT EXISTS cthulhu_global_pollution (
            pollution_id TEXT PRIMARY KEY,
            activated_at TEXT,
            triggered_by_name_id INTEGER
        );

        CREATE TABLE IF NOT EXISTS user_cthulhu_state (
            user_id TEXT PRIMARY KEY,
            current_san INTEGER NOT NULL DEFAULT 50,
            max_san INTEGER NOT NULL DEFAULT 50,
            is_in_deepdive_today INTEGER NOT NULL DEFAULT 0,
            pending_event_id TEXT,
            pending_event_tier TEXT,
            pending_event_force_pollute INTEGER NOT NULL DEFAULT 0,
            pending_event_choice TEXT,
            forced_pollution_until TEXT,
            pending_san_cap_tokens INTEGER NOT NULL DEFAULT 0,
            sci_fi_intervention_level INTEGER NOT NULL DEFAULT 0,
            last_daily_reset_at TEXT,
            pending_predict_candidates TEXT,
            pending_predict_expires_at TEXT
        );

        CREATE TABLE IF NOT EXISTS cthulhu_event_log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            event_id TEXT,
            choice_id TEXT,
            is_main_roll INTEGER NOT NULL DEFAULT 0,
            d100_roll INTEGER NOT NULL,
            result TEXT NOT NULL,
            san_delta INTEGER NOT NULL,
            granted_name_id INTEGER,
            occurred_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cthulhu_event_log_user_time
            ON cthulhu_event_log(user_id, occurred_at);
        """
    )

    authority_rows = [
        ("predict_upper", "predict", "upper"),
        ("predict_middle", "predict", "middle"),
        ("predict_lower", "predict", "lower"),
        ("time_upper", "time", "upper"),
        ("time_middle", "time", "middle"),
        ("time_lower", "time", "lower"),
        ("pollute_upper", "pollute", "upper"),
        ("pollute_middle", "pollute", "middle"),
        ("pollute_lower", "pollute", "lower"),
        ("sacrifice_upper", "sacrifice", "upper"),
        ("sacrifice_middle", "sacrifice", "middle"),
        ("sacrifice_lower", "sacrifice", "lower"),
    ]
    cursor.executemany(
        """
        INSERT OR IGNORE INTO cthulhu_authority(authority_id, god_type, tier, current_holder)
        VALUES (?, ?, ?, NULL)
        """,
        authority_rows,
    )

    pollution_rows = [("U1",), ("U2",), ("U5",), ("U8",), ("U10",), ("U11",), ("U14",)]
    cursor.executemany(
        "INSERT OR IGNORE INTO cthulhu_global_pollution(pollution_id, activated_at) VALUES (?, NULL)",
        pollution_rows,
    )

    item_rows = [
        (
            55,
            "深潜门票",
            "进入区域七时会被克苏鲁深潜系统自动消耗，用于开启当日深潜。",
            6,
            "进入区域七开始钓鱼时自动消耗，开启一次当日深潜。",
            0,
            1,
            "NONE",
            "{}",
        ),
        (
            56,
            "低语之露",
            "冰凉如夜潮的露滴，入口后理智会被勉强缝回一点。",
            6,
            "使用后恢复 5 点 SAN，不会超过当前上限。",
            0,
            1,
            "ADD_SAN",
            "{\"amount\": 5}",
        ),
        (
            57,
            "古旧瞳孔",
            "像鱼眼又像星眼的干瘪器官，被它看过的人会多承受一点真相。",
            8,
            "使用后永久提升 1 点 SAN 上限。",
            0,
            1,
            "ADD_MAX_SAN",
            "{\"amount\": 1}",
        ),
    ]
    cursor.executemany(
        """
        INSERT OR IGNORE INTO items
        (item_id, name, description, rarity, effect_description, cost, is_consumable, effect_type, effect_payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        item_rows,
    )

    cursor.execute(
        """
        UPDATE fishing_zones
        SET required_item_id = NULL, requires_pass = 0
        WHERE id = 7
        """
    )


def down(cursor):
    cursor.executescript(
        """
        DROP TABLE IF EXISTS cthulhu_event_log;
        DROP TABLE IF EXISTS user_cthulhu_state;
        DROP TABLE IF EXISTS cthulhu_global_pollution;
        DROP TABLE IF EXISTS cthulhu_authority;
        DROP TABLE IF EXISTS true_name_votes;
        DROP TABLE IF EXISTS true_names;
        DELETE FROM items WHERE item_id IN (55, 56, 57);
        UPDATE fishing_zones SET required_item_id = 16, requires_pass = 1 WHERE id = 7;
        """
    )
