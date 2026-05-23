"""迁移 013：为 user_cultivation 和 cthulhu_state 添加觉醒协议字段。"""

from __future__ import annotations


def up(conn) -> None:
    cur = conn.cursor()

    # user_cultivation 表添加四个觉醒协议字段
    cur.execute(
        """
        ALTER TABLE user_cultivation
        ADD COLUMN sci_fi_apex_singularity INTEGER NOT NULL DEFAULT 0
        """
    )
    cur.execute(
        """
        ALTER TABLE user_cultivation
        ADD COLUMN sci_fi_apex_abyss_unity INTEGER NOT NULL DEFAULT 0
        """
    )
    cur.execute(
        """
        ALTER TABLE user_cultivation
        ADD COLUMN sci_fi_apex_fate_solitude INTEGER NOT NULL DEFAULT 0
        """
    )
    cur.execute(
        """
        ALTER TABLE user_cultivation
        ADD COLUMN sci_fi_apex_resonance_summit INTEGER NOT NULL DEFAULT 0
        """
    )

    # cthulhu_state 表添加两个觉醒协议字段（singularity 和 fate_solitude 影响深渊偏移）
    cur.execute(
        """
        ALTER TABLE cthulhu_state
        ADD COLUMN sci_fi_apex_singularity INTEGER NOT NULL DEFAULT 0
        """
    )
    cur.execute(
        """
        ALTER TABLE cthulhu_state
        ADD COLUMN sci_fi_apex_fate_solitude INTEGER NOT NULL DEFAULT 0
        """
    )

    conn.commit()


def down(conn) -> None:
    cur = conn.cursor()

    # SQLite 不支持 DROP COLUMN，需要重建表
    # 这里简化处理，实际生产环境可能需要更复杂的回滚逻辑
    cur.execute(
        """
        CREATE TABLE user_cultivation_backup AS
        SELECT
            user_id, current_realm, current_realm_quality,
            accumulated_xiuwei, consecutive_failures,
            realm_history, tiancheng_protection,
            daily_observer_reward_count, daily_guard_reward_count,
            daily_count_reset_at, sci_fi_intervention_level,
            updated_at
        FROM user_cultivation
        """
    )
    cur.execute("DROP TABLE user_cultivation")
    cur.execute("ALTER TABLE user_cultivation_backup RENAME TO user_cultivation")

    cur.execute(
        """
        CREATE TABLE cthulhu_state_backup AS
        SELECT
            user_id, current_san, max_san, pending_san_cap_tokens,
            sci_fi_intervention_level, sci_fi_apex_abyss_unity,
            is_in_deepdive_today, pending_event_id, pending_event_tier,
            pending_event_force_pollute, pending_event_choice,
            pending_event_snapshot, pending_predict_candidates,
            daily_marker, updated_at
        FROM cthulhu_state
        """
    )
    cur.execute("DROP TABLE cthulhu_state")
    cur.execute("ALTER TABLE cthulhu_state_backup RENAME TO cthulhu_state")

    conn.commit()
