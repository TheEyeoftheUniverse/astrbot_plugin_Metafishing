import sqlite3
import threading
from typing import Optional, List, Dict, Any, Set
from datetime import datetime, timezone, timedelta
import json

from astrbot.api import logger
# 导入抽象基类和领域模型
from .abstract_repository import AbstractInventoryRepository
from ..domain.models import UserFishInventoryItem, UserAquariumItem, UserRodInstance, UserAccessoryInstance, FishingZone, AquariumUpgrade
from ..database.connection_manager import DatabaseConnectionManager

LOCAL_TIMEZONE = timezone(timedelta(hours=8))


def _to_local_naive_datetime(value) -> Optional[datetime]:
    """Return a UTC+8 naive datetime for values compared with core.utils.get_now()."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if dt.tzinfo is not None:
        dt = dt.astimezone(LOCAL_TIMEZONE).replace(tzinfo=None)
    return dt


class InsufficientFishQuantityError(Exception):
    """鱼类数量不足异常"""
    pass


class SqliteInventoryRepository(AbstractInventoryRepository):
    """用户库存仓储的SQLite实现"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._connection_manager = DatabaseConnectionManager(db_path)

    def _get_connection(self) -> sqlite3.Connection:
        """获取一个线程安全的数据库连接。"""
        return self._connection_manager.get_connection()

    def _record_equipment_obtained_with_cursor(
        self,
        cursor: sqlite3.Cursor,
        user_id: str,
        equipment_type: str,
        equipment_id: int,
        quantity: int = 1,
        obtained_at: Optional[datetime] = None,
    ) -> None:
        if quantity <= 0:
            return
        now = obtained_at or datetime.now()
        cursor.execute(
            """
            INSERT INTO user_equipment_stats (
                user_id, equipment_type, equipment_id,
                first_obtained_at, last_obtained_at, total_obtained
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, equipment_type, equipment_id) DO UPDATE SET
                last_obtained_at = excluded.last_obtained_at,
                total_obtained = total_obtained + excluded.total_obtained
            """,
            (user_id, equipment_type, equipment_id, now, now, quantity),
        )

    def record_equipment_obtained(
        self,
        user_id: str,
        equipment_type: str,
        equipment_id: int,
        quantity: int = 1,
        obtained_at: Optional[datetime] = None,
    ) -> None:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            self._record_equipment_obtained_with_cursor(
                cursor, user_id, equipment_type, equipment_id, quantity, obtained_at
            )
            conn.commit()

    # --- 私有映射辅助方法 ---
    def _row_to_fish_item(self, row: sqlite3.Row) -> Optional[UserFishInventoryItem]:
        if not row:
            return None
        return UserFishInventoryItem(
            user_id=row['user_id'],
            fish_id=row['fish_id'],
            quality_level=row['quality_level'],
            quantity=row['quantity']
        )

    def _row_to_aquarium_item(self, row: sqlite3.Row) -> Optional[UserAquariumItem]:
        if not row:
            return None
        return UserAquariumItem(
            user_id=row['user_id'],
            fish_id=row['fish_id'],
            quality_level=row['quality_level'],
            quantity=row['quantity'],
            added_at=row['added_at']
        )

    def _row_to_aquarium_upgrade(self, row: sqlite3.Row) -> Optional[AquariumUpgrade]:
        return None if not row else AquariumUpgrade(**row)

    def _row_to_rod_instance(self, row: sqlite3.Row) -> Optional[UserRodInstance]:
        if not row:
            return None
        # 手动映射字段，确保字段名匹配
        return UserRodInstance(
            rod_instance_id=row['rod_instance_id'],
            user_id=row['user_id'],
            rod_id=row['rod_id'],
            is_equipped=bool(row['is_equipped']),
            obtained_at=row['obtained_at'],
            refine_level=row['refine_level'] if 'refine_level' in row.keys() else 1,
            current_durability=row['current_durability'] if 'current_durability' in row.keys() else None,
            is_locked=bool(row['is_locked']) if 'is_locked' in row.keys() else False
        )

    def _row_to_accessory_instance(self, row: sqlite3.Row) -> Optional[UserAccessoryInstance]:
        if not row:
            return None
        # 手动映射字段，确保字段名匹配
        return UserAccessoryInstance(
            accessory_instance_id=row['accessory_instance_id'],
            user_id=row['user_id'],
            accessory_id=row['accessory_id'],
            is_equipped=bool(row['is_equipped']),
            obtained_at=row['obtained_at'],
            refine_level=row['refine_level'] if 'refine_level' in row.keys() else 1,
            is_locked=bool(row['is_locked']) if 'is_locked' in row.keys() else False
        )

    # --- Fish Inventory Methods ---
    def get_fish_inventory(self, user_id: str) -> List[UserFishInventoryItem]:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, fish_id, quality_level, quantity FROM user_fish_inventory WHERE user_id = ? AND quantity > 0", (user_id,))
            return [self._row_to_fish_item(row) for row in cursor.fetchall()]

    def get_fish_inventory_value(self, user_id: str, rarity: Optional[int] = None) -> int:
        query = """
            SELECT SUM(f.base_value * ufi.quantity * (1 + ufi.quality_level))
            FROM user_fish_inventory ufi
            JOIN fish f ON ufi.fish_id = f.fish_id
            WHERE ufi.user_id = ?
        """
        params = [user_id]
        if rarity is not None:
            query += " AND f.rarity = ?"
            params.append(rarity)

        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            result = cursor.fetchone()
            return result[0] if result and result[0] is not None else 0

    def add_fish_to_inventory(self, user_id: str, fish_id: int, quantity: int = 1, quality_level: int = 0) -> None:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO user_fish_inventory (user_id, fish_id, quality_level, quantity)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, fish_id, quality_level) DO UPDATE SET quantity = quantity + excluded.quantity
            """, (user_id, fish_id, quality_level, quantity))
            conn.commit()

    def clear_fish_inventory(self, user_id: str, rarity: Optional[int] = None) -> None:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            if rarity is None:
                cursor.execute("DELETE FROM user_fish_inventory WHERE user_id = ?", (user_id,))
            else:
                cursor.execute("""
                    DELETE FROM user_fish_inventory 
                    WHERE user_id = ? AND fish_id IN (
                        SELECT fish_id FROM fish WHERE rarity = ?
                    )
                """, (user_id, rarity))
            conn.commit()


    def sell_fish_keep_one(self, user_id: str) -> int:
        """
        执行“保留一条”的卖出数据库操作。
        返回卖出的总价值。
        注意：此操作应在一个事务中完成，以保证数据一致性。
        """
        sold_value = 0
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN TRANSACTION")
            try:
                # 查询所有数量大于1的鱼及其价值
                cursor.execute("""
                    SELECT ufi.fish_id, ufi.quantity, f.base_value, f.name
                    FROM user_fish_inventory ufi
                    JOIN fish f ON ufi.fish_id = f.fish_id
                    WHERE ufi.user_id = ? AND ufi.quantity > 1
                """, (user_id,))

                items_to_sell = cursor.fetchall()

                if not items_to_sell:
                    conn.rollback()
                    return 0

                for item in items_to_sell:
                    sell_qty = item["quantity"] - 1
                    sold_value += sell_qty * item["base_value"]

                # 将所有数量大于1的鱼更新为1
                cursor.execute("""
                    UPDATE user_fish_inventory
                    SET quantity = 1
                    WHERE user_id = ? AND quantity > 1
                """, (user_id,))

                conn.commit()
            except sqlite3.Error:
                conn.rollback()
                raise # 向上抛出异常，让服务层处理
        return sold_value

    def get_user_equipped_rod(self, user_id: str) -> Optional[UserRodInstance]:
        """获取用户当前装备的钓竿实例"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM user_rods
                WHERE user_id = ? AND is_equipped = 1
            """, (user_id,))
            row = cursor.fetchone()
            return self._row_to_rod_instance(row) if row else None

    def get_user_rod_instance_by_id(self, user_id: str, rod_instance_id: int) -> Optional[UserRodInstance]:
        """根据用户ID和钓竿实例ID获取特定的钓竿实例"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM user_rods
                WHERE user_id = ? AND rod_instance_id = ?
            """, (user_id, rod_instance_id))
            row = cursor.fetchone()
            return self._row_to_rod_instance(row) if row else None

    def clear_user_rod_instances(self, user_id: str) -> None:
        """清空用户的所有未装备且小于5星的钓竿实例"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM user_rods
                WHERE user_id = ? AND is_equipped = 0 AND rod_id IN (
                    SELECT rod_id FROM rods WHERE rarity < 5
                )
            """, (user_id,))
            conn.commit()

    def clear_user_accessory_instances(self, user_id: str) -> None:
        """清空用户的所有未装备且小于5星的配件实例"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM user_accessories
                WHERE user_id = ? AND is_equipped = 0 AND accessory_id IN (
                    SELECT accessory_id FROM accessories WHERE rarity < 5
                )
            """, (user_id,))
            conn.commit()

    def get_user_accessory_instance_by_id(self, user_id: str, accessory_instance_id: int) -> Optional[UserAccessoryInstance]:
        """根据用户ID和配件实例ID获取特定的配件实例"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM user_accessories
                WHERE user_id = ? AND accessory_instance_id = ?
            """, (user_id, accessory_instance_id))
            row = cursor.fetchone()
            return self._row_to_accessory_instance(row) if row else None

    def get_user_equipped_accessory(self, user_id: str) -> Optional[UserAccessoryInstance]:
        """获取用户当前装备的配件实例"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM user_accessories
                WHERE user_id = ? AND is_equipped = 1
            """, (user_id,))
            row = cursor.fetchone()
            return self._row_to_accessory_instance(row) if row else None

    def set_equipment_status(self, user_id: str, rod_instance_id: Optional[int] = None, accessory_instance_id: Optional[int] = None) -> None:
        """
        设置用户的装备状态。
        """
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            # 重置所有装备状态
            cursor.execute("""
                UPDATE user_rods SET is_equipped = 0 WHERE user_id = ?
            """, (user_id,))
            cursor.execute("""
                UPDATE user_accessories SET is_equipped = 0 WHERE user_id = ?
            """, (user_id,))

            # 设置新的装备状态
            if rod_instance_id is not None:
                cursor.execute("""
                    UPDATE user_rods SET is_equipped = 1 WHERE rod_instance_id = ? AND user_id = ?
                """, (rod_instance_id, user_id))
            if accessory_instance_id is not None:
                cursor.execute("""
                    UPDATE user_accessories SET is_equipped = 1 WHERE accessory_instance_id = ? AND user_id = ?
                """, (accessory_instance_id, user_id))

            conn.commit()


    def get_user_disposable_baits(self, user_id: str) -> List[int]:
        """
        获取用户的可用诱饵列表。
        返回一个包含诱饵ID的列表。
        """
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT bait_id FROM user_bait_inventory
                WHERE user_id = ? AND quantity > 0
            """, (user_id,))
            return [row["bait_id"] for row in cursor.fetchall()]

    def get_user_titles(self, user_id: str) -> List[int]:
        """
        获取用户拥有的称号列表。
        返回一个包含称号ID的列表。
        """
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT title_id FROM user_titles
                WHERE user_id = ?
            """, (user_id,))
            return [row["title_id"] for row in cursor.fetchall()]

    def get_random_bait(self, user_id: str) -> Optional[int]:
        """
        从用户的诱饵库存中随机获取一个可用的诱饵ID。
        如果没有可用诱饵，则返回None。
        """
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT bait_id FROM user_bait_inventory
                WHERE user_id = ? AND quantity > 0
                ORDER BY RANDOM() LIMIT 1
            """, (user_id,))
            row = cursor.fetchone()
            return row["bait_id"] if row else None

    # --- Bait Inventory Methods ---
    def get_user_bait_inventory(self, user_id: str) -> Dict[int, int]:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT bait_id, quantity FROM user_bait_inventory WHERE user_id = ?", (user_id,))
            return {row["bait_id"]: row["quantity"] for row in cursor.fetchall()}

    def update_bait_quantity(self, user_id: str, bait_id: int, delta: int) -> None:
        """更新用户诱饵库存中特定诱饵的数量（可增可减），并确保数量不小于0。"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO user_bait_inventory (user_id, bait_id, quantity)
                VALUES (?, ?, MAX(0, ?))
                ON CONFLICT(user_id, bait_id) DO UPDATE SET quantity = MAX(0, quantity + ?)
            """, (user_id, bait_id, delta, delta))
            if delta > 0:
                self._record_equipment_obtained_with_cursor(cursor, user_id, "bait", bait_id, delta)
            # 删除数量为0的行，保持数据整洁
            cursor.execute("DELETE FROM user_bait_inventory WHERE user_id = ? AND quantity <= 0", (user_id,))
            conn.commit()

    def sync_user_equipment_stats_from_inventory(self, user_id: str) -> None:
        """将当前装备库存作为图鉴初始解锁记录，重复执行不会累加数量。"""
        now = datetime.now()
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR IGNORE INTO user_equipment_stats (
                    user_id, equipment_type, equipment_id,
                    first_obtained_at, last_obtained_at, total_obtained
                )
                SELECT user_id, 'rod', rod_id, MIN(obtained_at), MAX(obtained_at), COUNT(*)
                FROM user_rods
                WHERE user_id = ?
                GROUP BY user_id, rod_id
                """,
                (user_id,),
            )
            cursor.execute(
                """
                INSERT OR IGNORE INTO user_equipment_stats (
                    user_id, equipment_type, equipment_id,
                    first_obtained_at, last_obtained_at, total_obtained
                )
                SELECT user_id, 'accessory', accessory_id, MIN(obtained_at), MAX(obtained_at), COUNT(*)
                FROM user_accessories
                WHERE user_id = ?
                GROUP BY user_id, accessory_id
                """,
                (user_id,),
            )
            cursor.execute(
                """
                INSERT OR IGNORE INTO user_equipment_stats (
                    user_id, equipment_type, equipment_id,
                    first_obtained_at, last_obtained_at, total_obtained
                )
                SELECT user_id, 'bait', bait_id, ?, ?, quantity
                FROM user_bait_inventory
                WHERE user_id = ? AND quantity > 0
                """,
                (now, now, user_id),
            )
            conn.commit()

    def get_user_equipment_stats(self, user_id: str) -> List[Dict[str, Any]]:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT user_id, equipment_type, equipment_id,
                       first_obtained_at, last_obtained_at, total_obtained
                FROM user_equipment_stats
                WHERE user_id = ?
                ORDER BY equipment_type, equipment_id
                """,
                (user_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_user_equipment_pokedex_reward_claims(self, user_id: str) -> List[Dict[str, Any]]:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT user_id, milestone_percent, reward_type, reward_amount,
                       claimed_unlocked_equipment_count, claimed_total_equipment_count, claimed_at
                FROM user_equipment_pokedex_reward_claims
                WHERE user_id = ?
                ORDER BY milestone_percent ASC
                """,
                (user_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def claim_equipment_pokedex_reward(
        self,
        user_id: str,
        milestone_percent: int,
        reward_type: str,
        reward_amount: int,
        unlocked_equipment_count: int,
        total_equipment_count: int,
    ) -> bool:
        claimed_at = datetime.now()
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO user_equipment_pokedex_reward_claims (
                        user_id,
                        milestone_percent,
                        reward_type,
                        reward_amount,
                        claimed_unlocked_equipment_count,
                        claimed_total_equipment_count,
                        claimed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        milestone_percent,
                        reward_type,
                        reward_amount,
                        unlocked_equipment_count,
                        total_equipment_count,
                        claimed_at,
                    ),
                )
                if cursor.rowcount == 0:
                    conn.rollback()
                    return False

                if reward_type == "coins":
                    cursor.execute(
                        """
                        UPDATE users
                        SET coins = coins + ?,
                            max_coins = MAX(max_coins, coins + ?)
                        WHERE user_id = ?
                        """,
                        (reward_amount, reward_amount, user_id),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE users
                        SET premium_currency = premium_currency + ?
                        WHERE user_id = ?
                        """,
                        (reward_amount, user_id),
                    )
                if cursor.rowcount == 0:
                    conn.rollback()
                    return False

                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise

    # --- Item Inventory Methods ---
    def get_user_item_inventory(self, user_id: str) -> Dict[int, int]:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT item_id, quantity FROM user_items WHERE user_id = ?", (user_id,))
            return {row["item_id"]: row["quantity"] for row in cursor.fetchall()}

    def add_item_to_user(self, user_id: str, item_id: int, quantity: int) -> None:
        """为用户添加指定数量的道具。"""
        self.update_item_quantity(user_id, item_id, quantity)

    def decrease_item_quantity(self, user_id: str, item_id: int, quantity: int) -> None:
        """减少用户道具库存中特定道具的数量。"""
        self.update_item_quantity(user_id, item_id, -quantity)

    def update_item_quantity(self, user_id: str, item_id: int, delta: int) -> None:
        """更新用户道具库存中特定道具的数量（可增可减），并确保数量不小于0。"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO user_items (user_id, item_id, quantity)
                VALUES (?, ?, MAX(0, ?))
                ON CONFLICT(user_id, item_id) DO UPDATE SET quantity = MAX(0, quantity + ?)
                """,
                (user_id, item_id, delta, delta),
            )
            cursor.execute("DELETE FROM user_items WHERE user_id = ? AND quantity <= 0", (user_id,))
            conn.commit()

    def _row_to_zone_stay(self, row: sqlite3.Row) -> Optional[Dict[str, Any]]:
        if not row:
            return None
        data = dict(row)
        expires_at = data.get("expires_at")
        data["expires_at"] = _to_local_naive_datetime(expires_at)
        return data

    def get_user_zone_stay(self, user_id: str, zone_id: int) -> Optional[Dict[str, Any]]:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM user_zone_stays WHERE user_id = ? AND zone_id = ?",
                (user_id, zone_id),
            )
            return self._row_to_zone_stay(cursor.fetchone())

    def upsert_user_zone_stay(self, user_id: str, zone_id: int, pass_item_id: int, expires_at: datetime) -> None:
        now = datetime.now(LOCAL_TIMEZONE).replace(tzinfo=None).isoformat()
        normalized_expires_at = _to_local_naive_datetime(expires_at)
        if normalized_expires_at is None:
            raise ValueError("expires_at must be a valid datetime")
        expires_at_text = normalized_expires_at.isoformat()
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO user_zone_stays (user_id, zone_id, pass_item_id, expires_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, zone_id) DO UPDATE SET
                    pass_item_id = excluded.pass_item_id,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (user_id, zone_id, pass_item_id, expires_at_text, now, now),
            )
            conn.commit()

    def delete_user_zone_stay(self, user_id: str, zone_id: int) -> None:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM user_zone_stays WHERE user_id = ? AND zone_id = ?",
                (user_id, zone_id),
            )
            conn.commit()

    def get_expired_zone_stays(self, now: datetime) -> List[Dict[str, Any]]:
        normalized_now = _to_local_naive_datetime(now)
        if normalized_now is None:
            normalized_now = datetime.now(LOCAL_TIMEZONE).replace(tzinfo=None)
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM user_zone_stays WHERE expires_at <= ?",
                (normalized_now.isoformat(),),
            )
            return [self._row_to_zone_stay(row) for row in cursor.fetchall()]

    # --- Rod Inventory Methods ---
    def get_user_rod_instances(self, user_id: str) -> List[UserRodInstance]:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM user_rods WHERE user_id = ?", (user_id,))
            return [self._row_to_rod_instance(row) for row in cursor.fetchall()]

    def  add_rod_instance(self, user_id: str, rod_id: int, durability: Optional[int], refine_level:int = 1) -> UserRodInstance:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now()
            cursor.execute("""
                INSERT INTO user_rods (user_id, rod_id, current_durability, obtained_at, refine_level, is_equipped, is_locked)
                VALUES (?, ?, ?, ?, ?, 0, 0)
            """, (user_id, rod_id, durability, now, refine_level))
            instance_id = cursor.lastrowid
            self._record_equipment_obtained_with_cursor(cursor, user_id, "rod", rod_id, 1, now)
            conn.commit()
            return UserRodInstance(
                rod_instance_id=instance_id, user_id=user_id, rod_id=rod_id,
                is_equipped=False, obtained_at=now, current_durability=durability, refine_level=refine_level, is_locked=False
            )

    def delete_rod_instance(self, rod_instance_id: int) -> None:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_rods WHERE rod_instance_id = ?", (rod_instance_id,))
            conn.commit()

    # --- Accessory Inventory Methods ---
    def get_user_accessory_instances(self, user_id: str) -> List[UserAccessoryInstance]:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM user_accessories WHERE user_id = ?", (user_id,))
            return [self._row_to_accessory_instance(row) for row in cursor.fetchall()]

    def add_accessory_instance(self, user_id: str, accessory_id: int, refine_level: int = 1) -> UserAccessoryInstance:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now()
            cursor.execute("""
                INSERT INTO user_accessories (user_id, accessory_id, obtained_at, refine_level, is_equipped, is_locked)
                VALUES (?, ?, ?, ?, 0, 0)
            """, (user_id, accessory_id, now, refine_level))
            instance_id = cursor.lastrowid
            self._record_equipment_obtained_with_cursor(cursor, user_id, "accessory", accessory_id, 1, now)
            conn.commit()
            return UserAccessoryInstance(
                accessory_instance_id=instance_id, user_id=user_id, accessory_id=accessory_id,
                is_equipped=False, obtained_at=now, refine_level=refine_level, is_locked=False
            )

    def delete_accessory_instance(self, accessory_instance_id: int) -> None:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_accessories WHERE accessory_instance_id = ?", (accessory_instance_id,))
            conn.commit()
            
    def update_fish_quantity(self, user_id: str, fish_id: int, delta: int, quality_level: int = 0) -> None:
        """更新用户鱼类库存中特定鱼的数量（可增可减），并确保数量不小于0。"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO user_fish_inventory (user_id, fish_id, quality_level, quantity)
                VALUES (?, ?, ?, MAX(0, ?))
                ON CONFLICT(user_id, fish_id, quality_level) DO UPDATE SET quantity = MAX(0, quantity + ?)
            """, (user_id, fish_id, quality_level, delta, delta))
            # 删除数量为0的行，保持数据整洁
            cursor.execute("DELETE FROM user_fish_inventory WHERE user_id = ? AND quantity <= 0", (user_id,))
            conn.commit()

    def get_zone_by_id(self, zone_id: int) -> FishingZone:
        """根据ID获取钓鱼区域信息"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM fishing_zones WHERE id = ?", (zone_id,))
            row = cursor.fetchone()
            if row:
                row_dict = dict(row)
                if 'configs' in row_dict and isinstance(row_dict['configs'], str):
                    row_dict['configs'] = json.loads(row_dict['configs'])
                for key in ('available_from', 'available_until'):
                    val = row_dict.get(key)
                    row_dict[key] = _to_local_naive_datetime(val)
                zone = FishingZone(**row_dict)
                zone.specific_fish_ids = self.get_specific_fish_ids_for_zone(zone.id)
                return zone
            else:
                raise ValueError(f"钓鱼区域ID {zone_id} 不存在。")
    def update_fishing_zone(self, zone: FishingZone) -> None:
        """更新钓鱼区域信息"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE fishing_zones
                SET name = ?, description = ?, daily_rare_fish_quota = ?, rare_fish_caught_today = ?
                WHERE id = ?
            """, (zone.name, zone.description, zone.daily_rare_fish_quota, zone.rare_fish_caught_today, zone.id))
            conn.commit()

    def get_all_zones(self) -> List[FishingZone]:
        """获取所有钓鱼区域信息"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM fishing_zones")
            
            zones = []
            for row in cursor.fetchall():
                row_dict = dict(row)
                if 'configs' in row_dict and isinstance(row_dict['configs'], str):
                    row_dict['configs'] = json.loads(row_dict['configs'])
                # 解析时间字段
                for key in ('available_from', 'available_until'):
                    val = row_dict.get(key)
                    row_dict[key] = _to_local_naive_datetime(val)
                zone = FishingZone(**row_dict)
                # 加载限定鱼
                zone.specific_fish_ids = self.get_specific_fish_ids_for_zone(zone.id)
                zones.append(zone)
            return zones

    def update_zone_configs(self, zone_id: int, configs: str) -> None:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE fishing_zones SET configs = ? WHERE id = ?",
                (configs, zone_id)
            )
            conn.commit()

    def create_zone(self, zone_data: Dict[str, Any]) -> FishingZone:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO fishing_zones (id, name, description, daily_rare_fish_quota, configs, is_active, available_from, available_until, required_item_id, requires_pass, fishing_cost)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    zone_data['id'],
                    zone_data['name'],
                    zone_data['description'],
                    zone_data['daily_rare_fish_quota'],
                    json.dumps(zone_data.get('configs', {})),
                    zone_data.get('is_active', True),
                    zone_data.get('available_from'),
                    zone_data.get('available_until'),
                    zone_data.get('required_item_id'),
                    zone_data.get('requires_pass', False),
                    zone_data.get('fishing_cost', 10)
                ))
                conn.commit()
            except sqlite3.IntegrityError as e:
                if "UNIQUE constraint failed: fishing_zones.id" in str(e):
                    raise ValueError(f"钓鱼区域 ID {zone_data['id']} 已存在。")
                else:
                    raise
            
            return self.get_zone_by_id(zone_data['id'])

    def update_zone(self, zone_id: int, zone_data: Dict[str, Any]) -> None:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE fishing_zones
                SET name = ?, description = ?, daily_rare_fish_quota = ?, configs = ?, is_active = ?, available_from = ?, available_until = ?, required_item_id = ?, requires_pass = ?, fishing_cost = ?
                WHERE id = ?
            """, (
                zone_data['name'],
                zone_data['description'],
                zone_data['daily_rare_fish_quota'],
                json.dumps(zone_data.get('configs', {})),
                zone_data.get('is_active', True),
                zone_data.get('available_from'),
                zone_data.get('available_until'),
                zone_data.get('required_item_id'),
                zone_data.get('requires_pass', False),
                zone_data.get('fishing_cost', 10),
                zone_id
            ))
            conn.commit()

    def delete_zone(self, zone_id: int) -> None:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM fishing_zones WHERE id = ?", (zone_id,))
            conn.commit()

    def get_specific_fish_ids_for_zone(self, zone_id: int) -> List[int]:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT fish_id FROM zone_fish_mapping WHERE zone_id = ?", (zone_id,))
            return [row[0] for row in cursor.fetchall()]

    def update_specific_fish_for_zone(self, zone_id: int, fish_ids: List[int]) -> None:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM zone_fish_mapping WHERE zone_id = ?", (zone_id,))
            if fish_ids:
                cursor.executemany("INSERT INTO zone_fish_mapping (zone_id, fish_id) VALUES (?, ?)",
                                   [(zone_id, fish_id) for fish_id in fish_ids])
            conn.commit()

    def update_rod_instance(self, rod_instance: UserRodInstance):
        """更新钓竿实例信息"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE user_rods
                SET rod_id = ?, is_equipped = ?, current_durability = ?, refine_level = ?, is_locked = ?
                WHERE rod_instance_id = ? AND user_id = ?
            """, (rod_instance.rod_id, rod_instance.is_equipped, rod_instance.current_durability, rod_instance.refine_level, rod_instance.is_locked, rod_instance.rod_instance_id, rod_instance.user_id))
            conn.commit()

    def transfer_rod_instance_ownership(self, rod_instance_id: int, new_user_id: str) -> None:
        """转移鱼竿实例所有权"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            
            # 确保目标用户存在（特别是系统用户如"MARKET"）
            cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (new_user_id,))
            if cursor.fetchone() is None:
                # 如果目标用户不存在，创建一个系统用户
                if new_user_id == "MARKET":
                    cursor.execute("""
                        INSERT INTO users (user_id, nickname, coins, premium_currency, created_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (new_user_id, "[系统-市场托管]", 0, 0, datetime.now()))
                    logger.info(f"自动创建系统用户: {new_user_id}")
                else:
                    raise ValueError(f"目标用户 {new_user_id} 不存在")
            
            cursor.execute("""
                UPDATE user_rods
                SET user_id = ?, is_equipped = 0
                WHERE rod_instance_id = ?
            """, (new_user_id, rod_instance_id))
            conn.commit()

    def update_accessory_instance(self, accessory_instance: UserAccessoryInstance):
        """更新配件实例信息"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE user_accessories
                SET accessory_id = ?, is_equipped = ?, refine_level = ?, is_locked = ?
                WHERE accessory_instance_id = ? AND user_id = ?
            """, (accessory_instance.accessory_id, accessory_instance.is_equipped, accessory_instance.refine_level, accessory_instance.is_locked, accessory_instance.accessory_instance_id, accessory_instance.user_id))
            conn.commit()

    def transfer_accessory_instance_ownership(self, accessory_instance_id: int, new_user_id: str) -> None:
        """转移饰品实例所有权"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            
            # 确保目标用户存在（特别是系统用户如"MARKET"）
            cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (new_user_id,))
            if cursor.fetchone() is None:
                # 如果目标用户不存在，创建一个系统用户
                if new_user_id == "MARKET":
                    cursor.execute("""
                        INSERT INTO users (user_id, nickname, coins, premium_currency, created_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (new_user_id, "[系统-市场托管]", 0, 0, datetime.now()))
                    logger.info(f"自动创建系统用户: {new_user_id}")
                else:
                    raise ValueError(f"目标用户 {new_user_id} 不存在")
            
            cursor.execute("""
                UPDATE user_accessories
                SET user_id = ?, is_equipped = 0
                WHERE accessory_instance_id = ?
            """, (new_user_id, accessory_instance_id))
            conn.commit()

    def get_same_rod_instances(self, user_id: int, rod_id: str) -> List[UserRodInstance]:
        """获取用户所有相同类型的钓竿实例"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM user_rods
                WHERE user_id = ? AND rod_id = ?
            """, (user_id, rod_id))
            return [self._row_to_rod_instance(row) for row in cursor.fetchall()]

    def get_same_accessory_instances(self, user_id: int, accessory_id: str) -> List[UserAccessoryInstance]:
        """获取用户所有相同类型的配件实例"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM user_accessories
                WHERE user_id = ? AND accessory_id = ?
            """, (user_id, accessory_id))
            return [self._row_to_accessory_instance(row) for row in cursor.fetchall()]

    # --- 水族箱相关方法 ---
    def get_aquarium_inventory(self, user_id: str) -> List[UserAquariumItem]:
        """获取用户水族箱中的鱼"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT user_id, fish_id, quality_level, quantity, added_at 
                FROM user_aquarium 
                WHERE user_id = ? AND quantity > 0
            """, (user_id,))
            return [self._row_to_aquarium_item(row) for row in cursor.fetchall()]

    def get_aquarium_inventory_value(self, user_id: str, rarity: Optional[int] = None) -> int:
        """获取用户水族箱中鱼的总价值"""
        query = """
            SELECT SUM(f.base_value * ua.quantity * (1 + ua.quality_level))
            FROM user_aquarium ua
            JOIN fish f ON ua.fish_id = f.fish_id
            WHERE ua.user_id = ?
        """
        params = [user_id]
        if rarity is not None:
            query += " AND f.rarity = ?"
            params.append(rarity)

        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            result = cursor.fetchone()
            return result[0] if result and result[0] is not None else 0

    def add_fish_to_aquarium(self, user_id: str, fish_id: int, quantity: int = 1, quality_level: int = 0) -> None:
        """向用户水族箱添加鱼"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO user_aquarium (user_id, fish_id, quality_level, quantity, added_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, fish_id, quality_level) DO UPDATE SET 
                    quantity = quantity + excluded.quantity
            """, (user_id, fish_id, quality_level, quantity))
            conn.commit()

    def remove_fish_from_aquarium(self, user_id: str, fish_id: int, quantity: int = 1, quality_level: int = 0) -> None:
        """从用户水族箱移除鱼
        Raises InsufficientFishQuantityError if not enough fish to remove.
        """
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE user_aquarium 
                SET quantity = quantity - ?
                WHERE user_id = ? AND fish_id = ? AND quality_level = ? AND quantity >= ?
            """, (quantity, user_id, fish_id, quality_level, quantity))
            
            if cursor.rowcount == 0:
                raise InsufficientFishQuantityError(
                    f"用户 {user_id} 水族箱中没有足够的鱼类 {fish_id}（品质等级 {quality_level}）来移除 {quantity} 个"
                )
            
            # 如果数量为0或负数，删除记录
            cursor.execute("""
                DELETE FROM user_aquarium 
                WHERE user_id = ? AND fish_id = ? AND quality_level = ? AND quantity <= 0
            """, (user_id, fish_id, quality_level))
            conn.commit()

    def update_aquarium_fish_quantity(self, user_id: str, fish_id: int, delta: int, quality_level: int = 0) -> None:
        """更新用户水族箱中鱼的数量"""
        if delta > 0:
            self.add_fish_to_aquarium(user_id, fish_id, delta, quality_level)
        elif delta < 0:
            self.remove_fish_from_aquarium(user_id, fish_id, -delta, quality_level)

    def clear_aquarium_inventory(self, user_id: str, rarity: Optional[int] = None) -> None:
        """清空用户水族箱"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            if rarity is None:
                cursor.execute("DELETE FROM user_aquarium WHERE user_id = ?", (user_id,))
            else:
                cursor.execute("""
                    DELETE FROM user_aquarium 
                    WHERE user_id = ? AND fish_id IN (
                        SELECT fish_id FROM fish WHERE rarity = ?
                    )
                """, (user_id, rarity))
            conn.commit()

    def get_aquarium_total_count(self, user_id: str) -> int:
        """获取用户水族箱中鱼的总数量"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT SUM(quantity) FROM user_aquarium WHERE user_id = ?
            """, (user_id,))
            result = cursor.fetchone()
            return result[0] if result and result[0] is not None else 0

    def get_user_total_fish_count(self, user_id: str, fish_id: int) -> int:
        """获取用户指定鱼类的总数量（包括鱼塘和水族箱）"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            
            # 获取鱼塘中的数量
            cursor.execute("""
                SELECT COALESCE(SUM(quantity), 0) FROM user_fish_inventory 
                WHERE user_id = ? AND fish_id = ?
            """, (user_id, fish_id))
            pond_count = cursor.fetchone()[0]
            
            # 获取水族箱中的数量
            cursor.execute("""
                SELECT COALESCE(SUM(quantity), 0) FROM user_aquarium 
                WHERE user_id = ? AND fish_id = ?
            """, (user_id, fish_id))
            aquarium_count = cursor.fetchone()[0]
            
            return pond_count + aquarium_count

    def get_user_fish_counts_in_bulk(self, user_id: str, fish_ids: Set[int]) -> Dict[int, int]:
        """
        一次性批量获取用户多种鱼类的总数（鱼塘+水族箱）。
        返回一个 {fish_id: total_count} 的字典。
        """
        if not fish_ids:
            return {}

        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            placeholders = ','.join('?' for _ in fish_ids)
            # 使用 UNION ALL + GROUP BY 一次性查询两个表
            query = f"""
                SELECT fish_id, SUM(quantity) as total_count
                FROM (
                    SELECT fish_id, quantity FROM user_fish_inventory 
                    WHERE user_id = ? AND fish_id IN ({placeholders})
                    UNION ALL
                    SELECT fish_id, quantity FROM user_aquarium 
                    WHERE user_id = ? AND fish_id IN ({placeholders})
                )
                GROUP BY fish_id
            """
            
            params = [user_id] + list(fish_ids) + [user_id] + list(fish_ids)
            cursor.execute(query, params)
            
            # 将结果转换为字典，并为没有查到的鱼类ID补0
            results = {row['fish_id']: row['total_count'] for row in cursor.fetchall()}
            for fish_id in fish_ids:
                if fish_id not in results:
                    results[fish_id] = 0
            return results

    def deduct_fish_smart(self, user_id: str, fish_id: int, quantity: int, quality_level: int = 0) -> None:
        """
        智能扣除指定品质的鱼类：优先从鱼塘扣除，不足时从水族箱扣除。
        """
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN TRANSACTION")
            try:
                # 1. 获取鱼塘中特定品质鱼的数量
                cursor.execute("""
                    SELECT quantity FROM user_fish_inventory 
                    WHERE user_id = ? AND fish_id = ? AND quality_level = ?
                """, (user_id, fish_id, quality_level))
                pond_row = cursor.fetchone()
                pond_count = pond_row['quantity'] if pond_row else 0
                
                remaining_qty = quantity
                
                # 2. 优先从鱼塘扣除
                if pond_count > 0:
                    deduct_from_pond = min(pond_count, remaining_qty)
                    if deduct_from_pond > 0:
                        # 调用现有的 update_fish_quantity 方法来处理扣除和删除空记录
                        self.update_fish_quantity(user_id, fish_id, -deduct_from_pond, quality_level)
                        remaining_qty -= deduct_from_pond
                
                # 3. 如果还需要更多，从水族箱扣除
                if remaining_qty > 0:
                    # 调用现有的 update_aquarium_fish_quantity 方法来处理扣除
                    try:
                        self.update_aquarium_fish_quantity(user_id, fish_id, -remaining_qty, quality_level)
                    except InsufficientFishQuantityError:
                        raise ValueError(
                            f"逻辑错误：用户 {user_id} 的鱼类 {fish_id} (品质 {quality_level}) "
                            f"总数不足以扣除 {quantity}，但在扣除阶段发现不足。"
                        )

                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def get_aquarium_upgrades(self) -> List[AquariumUpgrade]:
        """获取所有水族箱升级配置"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT upgrade_id, level, capacity, cost_coins, cost_premium, description, created_at
                FROM aquarium_upgrades 
                ORDER BY level
            """)
            return [self._row_to_aquarium_upgrade(row) for row in cursor.fetchall()]

    def get_aquarium_upgrade_by_level(self, level: int) -> Optional[AquariumUpgrade]:
        """根据等级获取水族箱升级配置"""
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT upgrade_id, level, capacity, cost_coins, cost_premium, description, created_at
                FROM aquarium_upgrades 
                WHERE level = ?
            """, (level,))
            row = cursor.fetchone()
            return self._row_to_aquarium_upgrade(row) if row else None
