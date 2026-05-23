from __future__ import annotations

import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError
from astrbot.api import logger

from ..database.sqlite_utils import connect_sqlite
from ..utils import get_now


LOCAL_TIMEZONE = timezone(timedelta(hours=8))
INVITATION_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
INVITATION_CODE_LENGTH = 8
WEBUI_PASSWORD_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789abcdefghjkmnpqrstuvwxyz"
WEBUI_PASSWORD_LENGTH = 6
DEFAULT_INVITATION_FAILURE_LIMIT = 5
DEFAULT_INVITATION_FAILURE_COOLDOWN_SECONDS = 600
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9]+$")
NICKNAME_PATTERN = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff]+$")


class AccountService:
    def __init__(
        self,
        db_path: str,
        user_repo,
        item_template_repo,
        user_service,
        config: Dict[str, Any],
    ):
        self.db_path = db_path
        self.user_repo = user_repo
        self.item_template_repo = item_template_repo
        self.user_service = user_service
        self.config = config or {}
        self.password_hasher = PasswordHasher()

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(
            self.db_path,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
            row_factory=sqlite3.Row,
        )

    def _registration_config(self) -> Dict[str, Any]:
        registration = ((self.config.get("webui") or {}).get("registration") or {})
        return {
            "enabled": bool(registration.get("enabled", True)),
            "require_invitation": bool(registration.get("require_invitation", True)),
            "username_min_length": int(registration.get("username_min_length", 5) or 5),
            "username_max_length": int(registration.get("username_max_length", 12) or 12),
            "password_min_length": WEBUI_PASSWORD_LENGTH,
            "invitation_quota": int(registration.get("invitation_quota", 5) or 5),
            "invitation_cost_premium": int(registration.get("invitation_cost_premium", 15) or 15),
            "invitation_ttl_days": int(registration.get("invitation_ttl_days", 0) or 0),
            "ip_rate_limit_daily": int(registration.get("ip_rate_limit_daily", 3) or 3),
        }

    def get_registration_config(self) -> Dict[str, Any]:
        return dict(self._registration_config())

    def resolve_login_user_id(self, raw_user_id: str) -> str:
        candidate = str(raw_user_id or "").strip()
        if not candidate:
            return ""
        user = self.user_repo.get_by_id(candidate)
        if user:
            return user.user_id
        lowered = candidate.lower()
        if lowered != candidate:
            lowered_user = self.user_repo.get_by_id(lowered)
            if lowered_user:
                return lowered_user.user_id
        return candidate

    def has_password(self, user_id: str) -> bool:
        user = self.user_repo.get_by_id(user_id)
        return bool(user and getattr(user, "password_hash", None))

    def verify_user_password(self, user_id: str, password: str) -> bool:
        user = self.user_repo.get_by_id(user_id)
        password_hash = getattr(user, "password_hash", None) if user else None
        if not user or not password_hash or not password:
            return False
        try:
            verified = self.password_hasher.verify(password_hash, password)
            if verified and self.password_hasher.check_needs_rehash(password_hash):
                self._update_password_hash(user_id, self.password_hasher.hash(password))
            return verified
        except VerifyMismatchError:
            return False
        except (InvalidHash, VerificationError) as exc:
            logger.warning("用户 %s 的密码哈希校验失败: %s", user_id, exc)
            return False

    def set_user_password(self, user_id: str, new_password: str) -> None:
        password = self._validate_password(new_password)
        self._update_password_hash(user_id, self.password_hasher.hash(password))
        logger.info("用户 %s 已设置新的 WebUI 密码哈希", user_id)

    def clear_user_password(self, user_id: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE users SET password_hash = NULL WHERE user_id = ?", (user_id,))
            conn.commit()
        logger.info("用户 %s 的 WebUI 密码已清空，等待下次重新设置", user_id)

    def issue_new_webui_password(self, user_id: str) -> str:
        user = self.user_repo.get_by_id(user_id)
        if not user:
            raise ValueError("用户不存在")
        password = "".join(secrets.choice(WEBUI_PASSWORD_ALPHABET) for _ in range(WEBUI_PASSWORD_LENGTH))
        self._update_password_hash(user_id, self.password_hasher.hash(password))
        logger.info("用户 %s 已生成新的 WebUI 初始密码", user_id)
        return password

    def authenticate_password_login(self, raw_user_id: str, password: str) -> Dict[str, Any]:
        login_user_id = self.resolve_login_user_id(raw_user_id)
        user = self.user_repo.get_by_id(login_user_id)
        if not user:
            return {"success": False, "message": "🎣 你不是我们的钓鱼佬，去别处钓鱼吧！"}

        if not getattr(user, "password_hash", None):
            return {
                "success": False,
                "message": "该账号尚未领取 WebUI 初始密码，请私聊机器人使用“初始密码获取”。",
                "login_user_id": user.user_id,
                "user": user,
            }

        if not self.verify_user_password(user.user_id, password):
            return {
                "success": False,
                "message": "密钥错误",
                "login_user_id": user.user_id,
                "user": user,
            }

        return {
            "success": True,
            "message": f"欢迎回来，{user.nickname or user.user_id}！",
            "login_user_id": user.user_id,
            "user": user,
        }

    def register_webui_user(
        self,
        username: str,
        password: str,
        invitation_code: str,
        nickname: str,
        ip_address: str,
    ) -> Dict[str, Any]:
        registration_config = self._registration_config()
        if not registration_config["enabled"]:
            return {"success": False, "message": "当前站点未开放 WebUI 注册"}

        normalized_username = self._validate_username(username)
        validated_password = self._validate_password(password)
        validated_nickname = self._validate_nickname(nickname, normalized_username)
        normalized_code = str(invitation_code or "").strip().upper()
        client_ip = self._normalize_ip_address(ip_address)

        with self._connect() as conn:
            cursor = conn.cursor()
            if self._is_registration_ip_limited(cursor, client_ip, registration_config["ip_rate_limit_daily"]):
                logger.warning("IP %s 命中当日 WebUI 注册次数上限", client_ip)
                return {"success": False, "message": "今日注册次数已达上限"}

            cooldown_message = self._get_invitation_cooldown_message(cursor, client_ip)
            if cooldown_message:
                return {"success": False, "message": cooldown_message}

            if self.user_repo.check_exists(normalized_username):
                return {"success": False, "message": "该账号已被注册"}

            invited_by_user_id = None
            invite_row = None
            if registration_config["require_invitation"] or normalized_code:
                if not normalized_code:
                    return {"success": False, "message": "请输入邀请码"}
                invite_row = cursor.execute(
                    """
                    SELECT code, issuer_user_id, expires_at, used_by_user_id
                    FROM invitation_codes
                    WHERE code = ?
                    """,
                    (normalized_code,),
                ).fetchone()
                if not invite_row:
                    self._record_invitation_failure(cursor, client_ip)
                    conn.commit()
                    logger.warning("IP %s 提交了不存在的邀请码 %s", client_ip, normalized_code)
                    return {"success": False, "message": "邀请码不存在"}

                now_ts = self._now_timestamp()
                expires_at = invite_row["expires_at"]
                if expires_at is not None and int(expires_at) < now_ts:
                    self._record_invitation_failure(cursor, client_ip)
                    conn.commit()
                    logger.warning("IP %s 使用了过期邀请码 %s", client_ip, normalized_code)
                    return {"success": False, "message": "邀请码已过期"}

                if invite_row["used_by_user_id"]:
                    logger.warning("IP %s 尝试复用已使用邀请码 %s", client_ip, normalized_code)
                    return {"success": False, "message": "邀请码已被使用"}

                invited_by_user_id = str(invite_row["issuer_user_id"] or "").strip() or None

            password_hash = self.password_hasher.hash(validated_password)
            now = get_now()
            now_ts = self._now_timestamp()
            onboarding_templates = self.user_service._resolve_onboarding_templates()
            onboarding_gift = {
                "coins": self.user_service.STARTER_COINS,
                "rod_id": onboarding_templates["rod"].rod_id,
                "rod_name": onboarding_templates["rod"].name,
                "accessory_id": onboarding_templates["accessory"].accessory_id,
                "accessory_name": onboarding_templates["accessory"].name,
                "bait_id": onboarding_templates["bait"].bait_id,
                "bait_name": onboarding_templates["bait"].name,
                "bait_quantity": self.user_service.STARTER_BAIT_QUANTITY,
            }

            try:
                cursor.execute("BEGIN")
                cursor.execute(
                    """
                    INSERT INTO users (
                        user_id, created_at, nickname, coins, max_coins,
                        password_hash, auth_source, invited_by_user_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized_username,
                        now,
                        validated_nickname,
                        self.user_service.STARTER_COINS,
                        self.user_service.STARTER_COINS,
                        password_hash,
                        "webui",
                        invited_by_user_id,
                    ),
                )
                self._grant_onboarding_gift_with_cursor(cursor, normalized_username, onboarding_templates, now)
                if invite_row:
                    cursor.execute(
                        """
                        UPDATE invitation_codes
                        SET used_by_user_id = ?, used_at = ?
                        WHERE code = ? AND used_by_user_id IS NULL
                        """,
                        (normalized_username, now_ts, normalized_code),
                    )
                    if cursor.rowcount == 0:
                        raise ValueError("邀请码已被其他玩家抢先使用")
                cursor.execute(
                    """
                    INSERT INTO web_registration_success_audit (
                        ip_address, registered_user_id, day, created_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (client_ip, normalized_username, now.date().isoformat(), now_ts),
                )
                cursor.execute(
                    "DELETE FROM web_registration_invite_failures WHERE ip_address = ?",
                    (client_ip,),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                logger.warning("WebUI 注册写库冲突: user_id=%s error=%s", normalized_username, exc)
                return {"success": False, "message": "该账号已被注册"}
            except ValueError as exc:
                conn.rollback()
                logger.warning("WebUI 注册失败: user_id=%s error=%s", normalized_username, exc)
                return {"success": False, "message": str(exc)}
            except Exception as exc:
                conn.rollback()
                logger.error("WebUI 注册事务失败: user_id=%s error=%s", normalized_username, exc, exc_info=True)
                return {"success": False, "message": "注册失败，请稍后重试"}

        logger.info(
            "WebUI 注册成功: user_id=%s invited_by=%s ip=%s",
            normalized_username,
            invited_by_user_id or "",
            client_ip,
        )
        return {
            "success": True,
            "user_id": normalized_username,
            "nickname": validated_nickname,
            "auth_source": "webui",
            "message": self.user_service._build_onboarding_message(
                onboarding_gift["rod_name"],
                onboarding_gift["accessory_name"],
                onboarding_gift["bait_name"],
            ),
            "is_new_user": True,
            "show_popup": True,
            "onboarding_gift": onboarding_gift,
        }

    def create_invitation_code(self, issuer_user_id: str) -> Dict[str, Any]:
        user = self.user_repo.get_by_id(issuer_user_id)
        if not user:
            return {"success": False, "message": "用户不存在"}

        registration_config = self._registration_config()
        cost = registration_config["invitation_cost_premium"]
        quota = registration_config["invitation_quota"]

        with self._connect() as conn:
            cursor = conn.cursor()
            active_unused_count = self._count_active_unused_invitations(cursor, issuer_user_id)
            if active_unused_count >= quota:
                return {"success": False, "message": f"你当前未使用的邀请码已达上限（{quota} 枚）"}
            if int(getattr(user, "premium_currency", 0) or 0) < cost:
                return {"success": False, "message": f"钻石不足，生成邀请码需要 {cost} 钻石"}

            now_ts = self._now_timestamp()
            expires_at = None
            ttl_days = registration_config["invitation_ttl_days"]
            if ttl_days > 0:
                expires_at = now_ts + ttl_days * 86400

            try:
                cursor.execute("BEGIN")
                cursor.execute(
                    """
                    UPDATE users
                    SET premium_currency = premium_currency - ?
                    WHERE user_id = ? AND premium_currency >= ?
                    """,
                    (cost, issuer_user_id, cost),
                )
                if cursor.rowcount == 0:
                    raise ValueError(f"钻石不足，生成邀请码需要 {cost} 钻石")

                code = self._insert_unique_invitation_code(cursor, issuer_user_id, now_ts, expires_at)
                conn.commit()
            except ValueError as exc:
                conn.rollback()
                return {"success": False, "message": str(exc)}
            except Exception as exc:
                conn.rollback()
                logger.error("生成邀请码失败: issuer=%s error=%s", issuer_user_id, exc, exc_info=True)
                return {"success": False, "message": "生成邀请码失败，请稍后再试"}

        logger.info("用户 %s 生成邀请码 %s 成功，扣除 %s 钻石", issuer_user_id, code, cost)
        return {
            "success": True,
            "message": f"邀请码生成成功：{code}（已扣除 {cost} 钻石）",
            "code": code,
            "cost": cost,
        }

    def get_invitation_dashboard(self, user_id: str) -> Dict[str, Any]:
        registration_config = self._registration_config()
        user = self.user_repo.get_by_id(user_id)
        with self._connect() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(
                """
                SELECT
                    ic.code,
                    ic.created_at,
                    ic.expires_at,
                    ic.used_by_user_id,
                    ic.used_at,
                    u.nickname AS used_by_nickname
                FROM invitation_codes ic
                LEFT JOIN users u ON u.user_id = ic.used_by_user_id
                WHERE ic.issuer_user_id = ?
                ORDER BY ic.created_at DESC
                """,
                (user_id,),
            ).fetchall()

        now_ts = self._now_timestamp()
        active_codes: List[Dict[str, Any]] = []
        used_codes: List[Dict[str, Any]] = []
        expired_codes: List[Dict[str, Any]] = []
        for row in rows:
            item = {
                "code": row["code"],
                "created_at_label": self._format_timestamp(row["created_at"]),
                "expires_at_label": self._format_timestamp(row["expires_at"]),
                "used_at_label": self._format_timestamp(row["used_at"]),
                "used_by_user_id": row["used_by_user_id"],
                "used_by_nickname": row["used_by_nickname"] or row["used_by_user_id"] or "",
            }
            if row["used_by_user_id"]:
                used_codes.append(item)
                continue
            expires_at = row["expires_at"]
            if expires_at is not None and int(expires_at) < now_ts:
                expired_codes.append(item)
            else:
                active_codes.append(item)

        cost = registration_config["invitation_cost_premium"]
        quota = registration_config["invitation_quota"]
        can_generate = bool(user)
        disable_reason = ""
        if len(active_codes) >= quota:
            can_generate = False
            disable_reason = f"未使用邀请码已达上限（{quota} 枚）"
        elif user and int(getattr(user, "premium_currency", 0) or 0) < cost:
            can_generate = False
            disable_reason = f"钻石不足，需 {cost} 钻石"

        return {
            "active_codes": active_codes,
            "used_codes": used_codes,
            "expired_codes": expired_codes,
            "unused_count": len(active_codes),
            "quota": quota,
            "cost": cost,
            "can_generate": can_generate,
            "disable_reason": disable_reason,
        }

    def format_invitation_list_text(self, user_id: str) -> str:
        dashboard = self.get_invitation_dashboard(user_id)
        lines = [
            "🎫 你的邀请码列表",
            f"未使用：{dashboard['unused_count']}/{dashboard['quota']}",
        ]
        if dashboard["active_codes"]:
            lines.append("【未使用】")
            for item in dashboard["active_codes"]:
                suffix = f"，到期 {item['expires_at_label']}" if item["expires_at_label"] else ""
                lines.append(f"- {item['code']}{suffix}")
        if dashboard["used_codes"]:
            lines.append("【已使用】")
            for item in dashboard["used_codes"]:
                used_by = item["used_by_nickname"] or item["used_by_user_id"] or "未知玩家"
                used_at = item["used_at_label"] or "时间未知"
                lines.append(f"- {item['code']} -> {used_by}（{used_at}）")
        if dashboard["expired_codes"]:
            lines.append("【已过期】")
            for item in dashboard["expired_codes"]:
                expires_at = item["expires_at_label"] or "时间未知"
                lines.append(f"- {item['code']}（{expires_at}）")
        if len(lines) == 2:
            lines.append("你还没有生成过邀请码。")
        return "\n".join(lines)

    def _update_password_hash(self, user_id: str, password_hash: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE user_id = ?",
                (password_hash, user_id),
            )
            conn.commit()

    def _validate_username(self, username: str) -> str:
        value = str(username or "").strip().lower()
        registration_config = self._registration_config()
        if not value:
            raise ValueError("请输入账号")
        if len(value) < registration_config["username_min_length"] or len(value) > registration_config["username_max_length"]:
            raise ValueError(
                f"账号长度需为 {registration_config['username_min_length']}–{registration_config['username_max_length']} 位"
            )
        if not USERNAME_PATTERN.fullmatch(value):
            raise ValueError("账号仅允许英文字母和数字")
        if value.isdigit():
            raise ValueError("账号必须至少包含一个英文字母")
        return value

    def _validate_password(self, password: str) -> str:
        value = str(password or "").strip()
        minimum = self._registration_config()["password_min_length"]
        if len(value) < minimum or len(value) > 20:
            raise ValueError(f"密码长度需要为 {minimum}~20 字")
        return value

    def _validate_nickname(self, nickname: str, fallback_username: str) -> str:
        value = str(nickname or "").strip()
        if not value:
            return fallback_username
        if len(value) < 1 or len(value) > 16:
            raise ValueError("昵称长度需要为 1~16 字")
        if not NICKNAME_PATTERN.fullmatch(value):
            raise ValueError("昵称仅允许中文、英文字母和数字")
        return value

    def _grant_onboarding_gift_with_cursor(self, cursor: sqlite3.Cursor, user_id: str, templates: Dict[str, Any], now: datetime) -> None:
        rod_template = templates["rod"]
        accessory_template = templates["accessory"]
        bait_template = templates["bait"]

        cursor.execute(
            """
            INSERT INTO user_rods (
                user_id, rod_id, current_durability, obtained_at, refine_level, is_equipped, is_locked
            ) VALUES (?, ?, ?, ?, 1, 0, 0)
            """,
            (user_id, rod_template.rod_id, rod_template.durability, now),
        )
        self._record_equipment_obtained_with_cursor(cursor, user_id, "rod", rod_template.rod_id, 1, now)

        cursor.execute(
            """
            INSERT INTO user_accessories (
                user_id, accessory_id, obtained_at, refine_level, is_equipped, is_locked
            ) VALUES (?, ?, ?, 1, 0, 0)
            """,
            (user_id, accessory_template.accessory_id, now),
        )
        self._record_equipment_obtained_with_cursor(cursor, user_id, "accessory", accessory_template.accessory_id, 1, now)

        cursor.execute(
            """
            INSERT INTO user_bait_inventory (user_id, bait_id, quantity)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, bait_id) DO UPDATE SET quantity = quantity + excluded.quantity
            """,
            (user_id, bait_template.bait_id, self.user_service.STARTER_BAIT_QUANTITY),
        )
        self._record_equipment_obtained_with_cursor(
            cursor,
            user_id,
            "bait",
            bait_template.bait_id,
            self.user_service.STARTER_BAIT_QUANTITY,
            now,
        )

    def _record_equipment_obtained_with_cursor(
        self,
        cursor: sqlite3.Cursor,
        user_id: str,
        equipment_type: str,
        equipment_id: int,
        quantity: int,
        obtained_at: datetime,
    ) -> None:
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
            (user_id, equipment_type, equipment_id, obtained_at, obtained_at, quantity),
        )

    def _count_active_unused_invitations(self, cursor: sqlite3.Cursor, issuer_user_id: str) -> int:
        now_ts = self._now_timestamp()
        row = cursor.execute(
            """
            SELECT COUNT(*)
            FROM invitation_codes
            WHERE issuer_user_id = ?
              AND used_by_user_id IS NULL
              AND (expires_at IS NULL OR expires_at >= ?)
            """,
            (issuer_user_id, now_ts),
        ).fetchone()
        return int(row[0] or 0) if row else 0

    def _insert_unique_invitation_code(
        self,
        cursor: sqlite3.Cursor,
        issuer_user_id: str,
        created_at: int,
        expires_at: Optional[int],
    ) -> str:
        for _ in range(20):
            code = "".join(secrets.choice(INVITATION_CODE_ALPHABET) for _ in range(INVITATION_CODE_LENGTH))
            try:
                cursor.execute(
                    """
                    INSERT INTO invitation_codes (
                        code, issuer_user_id, created_at, expires_at, used_by_user_id, used_at
                    ) VALUES (?, ?, ?, ?, NULL, NULL)
                    """,
                    (code, issuer_user_id, created_at, expires_at),
                )
                return code
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("生成邀请码时出现过多冲突，请重试")

    def _is_registration_ip_limited(self, cursor: sqlite3.Cursor, ip_address: str, daily_limit: int) -> bool:
        day = get_now().date().isoformat()
        row = cursor.execute(
            """
            SELECT COUNT(*)
            FROM web_registration_success_audit
            WHERE ip_address = ? AND day = ?
            """,
            (ip_address, day),
        ).fetchone()
        return int(row[0] or 0) >= max(1, daily_limit)

    def _get_invitation_cooldown_message(self, cursor: sqlite3.Cursor, ip_address: str) -> str:
        row = cursor.execute(
            """
            SELECT failure_count, blocked_until
            FROM web_registration_invite_failures
            WHERE ip_address = ?
            """,
            (ip_address,),
        ).fetchone()
        if not row:
            return ""
        blocked_until = int(row["blocked_until"] or 0)
        if blocked_until <= self._now_timestamp():
            cursor.execute("DELETE FROM web_registration_invite_failures WHERE ip_address = ?", (ip_address,))
            return ""
        remaining_minutes = max(1, (blocked_until - self._now_timestamp() + 59) // 60)
        return f"邀请码错误次数过多，请 {remaining_minutes} 分钟后再试"

    def _record_invitation_failure(self, cursor: sqlite3.Cursor, ip_address: str) -> None:
        now_ts = self._now_timestamp()
        row = cursor.execute(
            """
            SELECT failure_count, last_failed_at
            FROM web_registration_invite_failures
            WHERE ip_address = ?
            """,
            (ip_address,),
        ).fetchone()
        failure_count = 1
        if row:
            last_failed_at = int(row["last_failed_at"] or 0)
            if now_ts - last_failed_at <= DEFAULT_INVITATION_FAILURE_COOLDOWN_SECONDS:
                failure_count = int(row["failure_count"] or 0) + 1
        blocked_until = 0
        if failure_count >= DEFAULT_INVITATION_FAILURE_LIMIT:
            blocked_until = now_ts + DEFAULT_INVITATION_FAILURE_COOLDOWN_SECONDS
        cursor.execute(
            """
            INSERT INTO web_registration_invite_failures (
                ip_address, failure_count, last_failed_at, blocked_until
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(ip_address) DO UPDATE SET
                failure_count = excluded.failure_count,
                last_failed_at = excluded.last_failed_at,
                blocked_until = excluded.blocked_until
            """,
            (ip_address, failure_count, now_ts, blocked_until),
        )

    def _normalize_ip_address(self, ip_address: str) -> str:
        value = str(ip_address or "").strip()
        return value or "unknown"

    def _format_timestamp(self, value: Any) -> str:
        if value in (None, "", 0):
            return ""
        try:
            dt = datetime.fromtimestamp(int(value), tz=timezone.utc).astimezone(LOCAL_TIMEZONE)
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""

    def _now_timestamp(self) -> int:
        return int(datetime.now(timezone.utc).timestamp())
