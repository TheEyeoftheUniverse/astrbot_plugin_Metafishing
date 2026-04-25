import json
import os
import random
import threading
import traceback
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from astrbot.api import logger

from ..repositories.abstract_repository import (
    AbstractUserRepository,
    AbstractInventoryRepository,
    AbstractItemTemplateRepository,
    AbstractLogRepository,
)
from ..utils import get_now


class ExpeditionService:
    """科学考察服务"""

    # 参与者加入科考所需的通行证物品 ID
    _JOIN_PASS_ITEM_IDS = {"short": 38, "medium": 39, "long": 40}

    def __init__(
        self,
        user_repo: AbstractUserRepository,
        inventory_repo: AbstractInventoryRepository,
        item_template_repo: AbstractItemTemplateRepository,
        log_repo: AbstractLogRepository,
        config: Dict[str, Any],
    ):
        self.user_repo = user_repo
        self.inventory_repo = inventory_repo
        self.item_template_repo = item_template_repo
        self.log_repo = log_repo
        self.config = config
        self._expedition_lock = threading.RLock()
        self._settle_timers: Dict[str, threading.Timer] = {}

        # 数据文件路径
        self.data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
        os.makedirs(self.data_dir, exist_ok=True)
        self.expeditions_file = os.path.join(self.data_dir, "active_expeditions.json")
        self.history_file = os.path.join(self.data_dir, "expedition_history.json")

    def _load_expeditions(self) -> Dict[str, Any]:
        """加载进行中的科考数据"""
        data = self._safe_load_json_with_backup(self.expeditions_file)
        if isinstance(data, dict):
            return data
        logger.error(f"科考数据文件内容类型异常，期望 dict，实际 {type(data)}")
        return {}

    def _save_expeditions(self, expeditions: Dict[str, Any]) -> None:
        """保存科考数据"""
        try:
            with self._expedition_lock:
                if not expeditions:
                    existing = self._try_load_json(self.expeditions_file)
                    if isinstance(existing, dict) and existing:
                        logger.error(
                            "检测到尝试用空对象覆盖非空科考数据，已阻止写入以避免丢档。\n"
                            + "".join(traceback.format_stack(limit=10))
                        )
                        return

                    logger.warning(
                        "即将写入空的科考数据（{}）。若非预期清空，请检查调用链。\n" + "".join(traceback.format_stack(limit=8))
                    )
                self._atomic_write_json_with_backup(self.expeditions_file, expeditions)
        except Exception as e:
            logger.error(f"保存科考数据失败: {e}")

    def _load_history(self) -> Dict[str, Any]:
        """加载科考历史记录"""
        data = self._safe_load_json_with_backup(self.history_file)
        if isinstance(data, dict):
            return data
        logger.error(f"科考历史文件内容类型异常，期望 dict，实际 {type(data)}")
        return {}

    def _save_history(self, history: Dict[str, Any]) -> None:
        """保存科考历史记录"""
        try:
            self._atomic_write_json_with_backup(self.history_file, history)
        except Exception as e:
            logger.error(f"保存科考历史失败: {e}")

    def _safe_load_json_with_backup(self, path: str) -> Any:
        """优先读取主文件；失败时回退读取 .bak。

        额外保护：如果主文件解析成功但内容为空 dict，而 .bak 有非空 dict，
        认为可能发生了异常覆盖，优先返回 .bak。
        """
        main = self._try_load_json(path)
        if isinstance(main, dict) and main:
            return main

        backup_path = f"{path}.bak"
        backup = self._try_load_json(backup_path)

        if isinstance(main, dict) and not main and isinstance(backup, dict) and backup:
            logger.warning(f"检测到 {os.path.basename(path)} 为空，但备份非空，已从备份回退加载")
            return backup

        if main is not None:
            return main
        if backup is not None:
            logger.warning(f"主文件 {os.path.basename(path)} 读取失败，已从备份回退加载")
            return backup
        return {}

    def _try_load_json(self, path: str) -> Any:
        if not os.path.exists(path):
            return None
        try:
            if os.path.getsize(path) <= 0:
                return {}
        except Exception:
            pass

        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"读取JSON失败: {path} - {e}")
            return None

    def _atomic_write_json_with_backup(self, path: str, data: Any) -> None:
        """原子写 JSON，并维护一个 .bak 备份，避免写入中断导致文件被截断。"""
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)

        tmp_path = f"{path}.tmp"
        bak_path = f"{path}.bak"
        bak_tmp_path = f"{bak_path}.tmp"

        payload = json.dumps(data, ensure_ascii=False, indent=2)

        # 先备份当前主文件内容（如果存在且可读）
        try:
            if os.path.exists(path):
                with open(path, "rb") as src:
                    existing = src.read()
                if existing:
                    with open(bak_tmp_path, "wb") as bf:
                        bf.write(existing)
                        bf.flush()
                        os.fsync(bf.fileno())
                    os.replace(bak_tmp_path, bak_path)
        except Exception as e:
            logger.warning(f"写入备份失败（将继续保存主文件）: {e}")

        # 原子写主文件
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)

    def _prune_storage_to_current_and_last(self) -> None:
        """仅保留所有进行中的科考，以及“每个队长”最近一条已结束科考。

        说明：如果只保留全局最新一条 ended，当多个队伍并行/先后结算时，
        其他队伍的 ended 会被清掉，造成“科考状态查不到上次结果”的体验。
        因此这里按 creator_id 分组，每个队长保留 1 条 ended。
        """
        try:
            with self._expedition_lock:
                expeditions = self._load_expeditions()
                if not expeditions:
                    return

                ended_by_creator: Dict[str, list] = {}
                for exp_id, exp in expeditions.items():
                    if exp.get("status", "active") != "ended":
                        continue
                    creator_id = exp.get("creator_id") or "unknown"
                    ended_at_str = exp.get("ended_at") or exp.get("end_time")
                    try:
                        ended_at = datetime.strptime(ended_at_str, "%Y-%m-%d %H:%M:%S") if ended_at_str else datetime.min
                    except Exception:
                        ended_at = datetime.min
                    ended_by_creator.setdefault(creator_id, []).append((exp_id, ended_at))

                # 对每个队长：仅保留最新一条 ended
                to_delete = []
                for creator_id, entries in ended_by_creator.items():
                    if len(entries) <= 1:
                        continue
                    entries.sort(key=lambda x: x[1], reverse=True)
                    for exp_id, _ in entries[1:]:
                        to_delete.append(exp_id)

                if not to_delete:
                    return

                for exp_id in to_delete:
                    expeditions.pop(exp_id, None)

                self._save_expeditions(expeditions)
        except Exception as e:
            logger.error(f"修剪科考存储失败: {e}")

    def _record_user_expedition_result(self, user_id: str, expedition: Dict[str, Any], reward: Dict[str, Any]) -> None:
        """记录用户的科考结算结果"""
        history = self._load_history()
        
        type_names = {"short": "探险", "medium": "征服", "long": "圣域"}
        
        history[user_id] = {
            "expedition_id": expedition.get("expedition_id", "unknown"),
            "expedition_type": type_names.get(expedition.get("type", ""), expedition.get("type", "")),
            "completion_rate": expedition.get("total_progress", 0),
            "contribution": reward.get("contribution", 0),
            "premium_reward": reward.get("premium", 0),
            "settled_at": get_now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        self._save_history(history)
        logger.info(f"已保存用户 {user_id} 的科考结算记录")

    @staticmethod
    def _is_expedition_completed(expedition: Dict[str, Any]) -> bool:
        """判断科考目标是否全部完成。"""
        if not isinstance(expedition, dict):
            return False

        targets = expedition.get("targets", {})
        if isinstance(targets, dict) and targets:
            for target in targets.values():
                if not isinstance(target, dict):
                    continue
                caught = int(target.get("caught", 0) or 0)
                required = int(target.get("required", 0) or 0)
                if caught < required:
                    return False
            return True

        return float(expedition.get("total_progress", 0.0) or 0.0) >= 1.0

    @staticmethod
    def _format_reward_preview(reward: Dict[str, Any]) -> str:
        if not isinstance(reward, dict):
            return ""

        premium = int(reward.get("premium", 0) or 0)
        if reward.get("claimed"):
            return f"已领取 {premium}钻石"
        if premium <= 0:
            return "无可领取奖励"
        return f"{premium}钻石"

    def _prepare_expedition_claims_locked(self, expedition: Dict[str, Any]) -> Dict[str, Any]:
        """将已完成的科考推进到可领取状态。

        调用约束：必须在持有 `_expedition_lock` 的前提下调用。
        """
        if not isinstance(expedition, dict):
            return {"changed": False, "status": "", "message": ""}

        status = expedition.get("status", "active")
        if status != "active":
            return {
                "changed": False,
                "status": status,
                "message": expedition.get("settlement_report", ""),
            }

        if not self._is_expedition_completed(expedition):
            return {"changed": False, "status": "active", "message": ""}

        now_str = get_now().strftime("%Y-%m-%d %H:%M:%S")

        total_contribution = 0
        for participant in expedition.get("participants", {}).values():
            contribution_map = participant.get("contribution", {}) if isinstance(participant, dict) else {}
            total_contribution += sum(int(amount or 0) for amount in contribution_map.values())

        if total_contribution == 0:
            report = "科考目标已完成，但无人贡献，无奖励可领取。"
            for user_id, participant in expedition.get("participants", {}).items():
                reward_stub = {
                    "nickname": participant.get("nickname", ""),
                    "contribution": 0,
                    "premium": 0,
                }
                self._record_user_expedition_result(user_id, expedition, reward_stub)

            expedition["status"] = "ended"
            expedition["completed_at"] = now_str
            expedition["ended_at"] = now_str
            expedition["settlement_report"] = report
            expedition["rewards"] = {}
            return {"changed": True, "status": "ended", "message": report}

        completed_rarities = []
        for target in expedition.get("targets", {}).values():
            if not isinstance(target, dict):
                continue
            if int(target.get("caught", 0) or 0) >= int(target.get("required", 0) or 0):
                completed_rarities.append(int(target.get("rarity", 0) or 0))

        completed_rarities = sorted(set(completed_rarities))
        event_results = []
        for rarity in completed_rarities:
            event_result = self._trigger_rarity_event(expedition, rarity)
            if event_result:
                event_results.append(event_result)

        completion_rate = float(expedition.get("total_progress", 0.0) or 0.0)
        type_premium_base = {"short": 1000, "medium": 5000, "long": 10000}
        base_premium = type_premium_base.get(expedition.get("type", ""), 1000)
        total_premium = int(base_premium * completion_rate)

        rewards = {}
        pending_claims = 0
        for user_id, participant in expedition.get("participants", {}).items():
            contribution_map = participant.get("contribution", {}) if isinstance(participant, dict) else {}
            user_contribution = sum(int(amount or 0) for amount in contribution_map.values())
            reward = {
                "nickname": participant.get("nickname", ""),
                "contribution": user_contribution,
                "premium": 0,
                "claimed": False,
                "claimed_at": "",
                "auto_claimed": False,
            }

            if user_contribution > 0:
                reward["premium"] = max(1, int(total_premium * (user_contribution / total_contribution)))
                pending_claims += 1
            else:
                reward["claimed"] = True
                reward["claimed_at"] = now_str
                reward["auto_claimed"] = True
                self._record_user_expedition_result(user_id, expedition, reward)

            rewards[user_id] = reward

        type_names = {"short": "探险", "medium": "征服", "long": "圣域"}
        report_lines = [
            f"🎉 {type_names.get(expedition.get('type', ''), '')}科考任务已完成！",
            "━━━━━━━━━━━━━━━━━━━━",
            f"📊 完成度：{completion_rate * 100:.1f}%",
            f"💎 总钻石奖励：{total_premium}",
        ]

        report_lines.append("")
        report_lines.append("✨ 特殊事件：")
        if event_results:
            report_lines.extend(event_results)
        else:
            report_lines.append("  本次科学考察无异象发生……")

        expedition["special_events"] = list(event_results)

        report_lines.append("")
        report_lines.append("👤 待领取奖励：")
        for reward in sorted(rewards.values(), key=lambda item: item["contribution"], reverse=True):
            status_text = "（已自动记账）" if reward.get("auto_claimed") else "（待领取）"
            report_lines.append(
                f"  {reward['nickname']}: "
                f"{int(reward.get('premium', 0) or 0)}钻石 {status_text}"
            )

        expedition["completed_at"] = now_str
        expedition["claimable_at"] = now_str
        expedition["settlement_report"] = "\n".join(report_lines)
        expedition["rewards"] = rewards
        expedition.pop("ended_at", None)

        if pending_claims > 0:
            expedition["status"] = "claimable"
            return {"changed": True, "status": "claimable", "message": expedition["settlement_report"]}

        expedition["status"] = "ended"
        expedition["ended_at"] = now_str
        return {"changed": True, "status": "ended", "message": expedition["settlement_report"]}

    def _generate_expedition_id(self) -> str:
        """生成科考ID"""
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"EXP{timestamp}{random.randint(100, 999)}"

    def _select_random_fish(self, rarity: int, zone_id: int = 1) -> Optional[Dict[str, Any]]:
        """从指定星级中随机选择一条鱼"""
        fishes = self.item_template_repo.get_fishes_by_rarity(rarity)
        if not fishes:
            return None

        selected_fish = random.choice(fishes)
        return {
            "fish_id": selected_fish.fish_id,
            "fish_name": selected_fish.name,
            "rarity": selected_fish.rarity
        }

    def _consume_join_pass(self, user_id: str, expedition_type: str) -> Optional[str]:
        """检查并消耗加入科考的通行证。

        Returns:
            None 表示通行证已成功消耗；否则返回失败原因文案。
        """
        pass_item_id = self._JOIN_PASS_ITEM_IDS.get(expedition_type)
        if pass_item_id is None:
            return "未知的科考类型"

        user_items = self.inventory_repo.get_user_item_inventory(user_id)
        item_count = user_items.get(pass_item_id, 0)
        if item_count < 1:
            item_template = self.item_template_repo.get_item_by_id(pass_item_id)
            item_name = item_template.name if item_template else "科考通行证"
            return f"需要1张{item_name}才能加入科考"

        self.inventory_repo.update_item_quantity(user_id, pass_item_id, -1)
        return None

    def create_expedition(
        self, 
        creator_id: str, 
        expedition_type: str,
        invited_users: List[str] = None
    ) -> Dict[str, Any]:
        """
        创建科考队伍
        
        Args:
            creator_id: 队长用户ID
            expedition_type: 科考类型 (short/medium/long)
            invited_users: 被邀请的用户ID列表
        """
        user = self.user_repo.get_by_id(creator_id)
        if not user:
            return {"success": False, "message": "用户不存在"}

        # 检查是否已在其他科考中
        if self.get_user_expedition(creator_id):
            return {"success": False, "message": "你已经在另一个科考队伍中了"}

        # 确定科考参数
        type_config = {
            "short": {
                "duration_hours": 24,
                "targets": 100,
                "base_reward": 100,
                "required_item_id": 35,  # 探险许可证
            },
            "medium": {
                "duration_hours": 48,
                "targets": 500,
                "base_reward": 500,
                "required_item_id": 36,  # 征服许可证
            },
            "long": {
                "duration_hours": 72,
                "targets": 1000,
                "base_reward": 1000,
                "required_item_id": 37,  # 圣域许可证
            },
        }

        if expedition_type not in type_config:
            return {"success": False, "message": "科考类型错误，请使用：探险、征服或圣域"}

        config = type_config[expedition_type]

        # 检查并消耗许可证
        required_item_id = config["required_item_id"]
        user_items = self.inventory_repo.get_user_item_inventory(creator_id)
        item_count = user_items.get(required_item_id, 0)
        
        if item_count < 1:
            item_template = self.item_template_repo.get_item_by_id(required_item_id)
            item_name = item_template.name if item_template else "许可证"
            return {"success": False, "message": f"需要消耗1个{item_name}才能发起科考"}
        
        # 消耗许可证
        self.inventory_repo.update_item_quantity(creator_id, required_item_id, -1)
        
        # 生成科考ID和邀请码
        expedition_id = self._generate_expedition_id()
        
        # 随机选择5种目标鱼（1-5星各一种）
        targets = {}
        # 4星和5星鱼的特殊目标数量
        four_star_targets = {"short": 50, "medium": 100, "long": 500}
        five_star_targets = {"short": 10, "medium": 50, "long": 100}
        
        for rarity in range(1, 6):
            fish = self._select_random_fish(rarity)
            if fish:
                # 4星和5星鱼使用特殊的目标数量，其他星级使用通用配置
                if rarity == 5:
                    required_count = five_star_targets[expedition_type]
                elif rarity == 4:
                    required_count = four_star_targets[expedition_type]
                else:
                    required_count = config["targets"]
                    
                targets[f"{rarity}_star"] = {
                    "fish_id": fish["fish_id"],
                    "fish_name": fish["fish_name"],
                    "rarity": rarity,
                    "required": required_count,
                    "caught": 0
                }

        if len(targets) != 5:
            return {"success": False, "message": "无法选择足够的目标鱼类"}

        # 创建科考数据
        now = get_now()
        end_time = now + timedelta(hours=config["duration_hours"])
        
        expedition = {
            "expedition_id": expedition_id,
            "type": expedition_type,
            "start_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "creator_id": creator_id,
            "creator_name": user.nickname or f"渔夫{creator_id[-4:]}",
            "base_reward": config["base_reward"],
            "targets": targets,
            "participants": {
                creator_id: {
                    "user_id": creator_id,
                    "nickname": user.nickname or f"渔夫{creator_id[-4:]}",
                    "joined_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "contribution": {
                        "1_star": 0,
                        "2_star": 0,
                        "3_star": 0,
                        "4_star": 0,
                        "5_star": 0
                    }
                }
            },
            "total_progress": 0.0,
            "status": "active",
            "rare_fish_caught": {}  # 记录成员钓起的6~10星鱼ID: {user_id: [fish_ids]}
        }

        # 自动添加被邀请的用户
        failed_invites = []  # 记录无法加入的用户

        if invited_users:
            for user_id in invited_users:
                if user_id == creator_id:
                    continue

                invited_user = self.user_repo.get_by_id(user_id)
                if not invited_user:
                    continue

                # 检查用户是否已在其他科考中
                if self.get_user_expedition(user_id):
                    failed_invites.append((invited_user.nickname or f"渔夫{user_id[-4:]}", "已在其他科考中"))
                    continue

                # 检查并消耗通行证
                pass_error = self._consume_join_pass(user_id, expedition_type)
                if pass_error:
                    failed_invites.append((invited_user.nickname or f"渔夫{user_id[-4:]}", pass_error))
                    continue

                # 添加到科考队伍
                expedition["participants"][user_id] = {
                    "user_id": user_id,
                    "nickname": invited_user.nickname or f"渔夫{user_id[-4:]}",
                    "joined_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "contribution": {
                        "1_star": 0,
                        "2_star": 0,
                        "3_star": 0,
                        "4_star": 0,
                        "5_star": 0
                    }
                }

        # 保存科考数据
        with self._expedition_lock:
            expeditions = self._load_expeditions()
            expeditions[expedition_id] = expedition
            self._save_expeditions(expeditions)

        # 生成目标鱼列表文本
        targets_text = "\n".join([
            f"  {'⭐' * t['rarity']} {t['fish_name']}：0/{t['required']}"
            for t in targets.values()
        ])

        type_names = {"short": "探险", "medium": "征服", "long": "圣域"}
        
        # 构建返回消息
        success_count = len(expedition["participants"]) - 1  # 减去队长
        message = (f"🔬 {type_names[expedition_type]}科考已发起！\n"
                  f"📋 邀请码：{expedition_id}\n"
                  f"⏰ 截止时间：{end_time.strftime('%m-%d %H:%M')}\n"
                  f"🎯 目标鱼类：\n{targets_text}\n\n")

        # 添加邀请结果信息
        if invited_users:
            if success_count > 0:
                message += f"✅ {success_count}位成员已自动加入\n"
            if failed_invites:
                message += f"❌ {len(failed_invites)}位成员无法加入：\n"
                for name, reason in failed_invites:
                    message += f"  • {name}（{reason}）\n"
            message += "\n"
        
        message += f"其他成员可使用 /加入科考 {expedition_id} 加入队伍"
        
        return {
            "success": True,
            "message": message,
            "expedition_id": expedition_id
        }

    def join_expedition(self, user_id: str, expedition_id: str) -> Dict[str, Any]:
        """加入科考队伍"""
        user = self.user_repo.get_by_id(user_id)
        if not user:
            return {"success": False, "message": "用户不存在"}

        with self._expedition_lock:
            # 检查是否已在其他科考中
            current_exp = self.get_user_expedition(user_id)
            if current_exp:
                return {"success": False, "message": "你已经在另一个科考队伍中了"}

            # 加载科考数据
            expeditions = self._load_expeditions()
            if expedition_id not in expeditions:
                return {"success": False, "message": "科考不存在或已结束"}

            expedition = expeditions[expedition_id]

            # 检查科考状态
            if expedition["status"] != "active":
                return {"success": False, "message": "该科考已结束"}

            # 检查是否已过期
            end_time = datetime.strptime(expedition["end_time"], "%Y-%m-%d %H:%M:%S")
            now = get_now()
            
            if now > end_time:
                return {"success": False, "message": "该科考已过期"}

            # 检查是否已在队伍中
            if user_id in expedition["participants"]:
                return {"success": False, "message": "你已经在这个科考队伍中了"}

            # 检查并消耗通行证
            pass_error = self._consume_join_pass(user_id, expedition.get("type", ""))
            if pass_error:
                return {"success": False, "message": pass_error}

            # 添加成员
            now = get_now()
            expedition["participants"][user_id] = {
                "user_id": user_id,
                "nickname": user.nickname or f"渔夫{user_id[-4:]}",
                "joined_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                "contribution": {
                    "1_star": 0,
                    "2_star": 0,
                    "3_star": 0,
                    "4_star": 0,
                    "5_star": 0
                }
            }

            # 保存
            expeditions[expedition_id] = expedition
            self._save_expeditions(expeditions)

            return {
                "success": True,
                "message": f"✅ 成功加入科考队伍！\n"
                          f"队长：{expedition['creator_name']}\n"
                          f"当前成员：{len(expedition['participants'])}人"
            }

    def leave_expedition(self, user_id: str) -> Dict[str, Any]:
        """退出科考队伍"""
        with self._expedition_lock:
            expedition = self.get_user_expedition(user_id)
            if not expedition:
                return {"success": False, "message": "你不在任何科考队伍中"}

            expedition_id = expedition["expedition_id"]
            
            # 队长不能退出
            if user_id == expedition["creator_id"]:
                return {"success": False, "message": "队长不能退出科考，请使用 /结束科考 来结束考察"}

            # 移除成员（保留贡献记录）
            expeditions = self._load_expeditions()
            if expedition_id in expeditions:
                if user_id in expeditions[expedition_id]["participants"]:
                    del expeditions[expedition_id]["participants"][user_id]
                    self._save_expeditions(expeditions)

            return {"success": True, "message": "已退出科考队伍（你的贡献已保留，但不会获得最终奖励）"}

    def get_user_expedition(self, user_id: str) -> Optional[Dict[str, Any]]:
        """获取用户当前参与的未完成科考。"""
        expeditions = self._load_expeditions()
        for exp in expeditions.values():
            if user_id in exp["participants"] and exp["status"] == "active":
                return exp
        return None

    def get_user_claimable_expeditions(self, user_id: str) -> List[Dict[str, Any]]:
        """获取用户仍可领取奖励的科考列表。"""
        expeditions = self._load_expeditions()
        claimable = []
        for exp in expeditions.values():
            if exp.get("status") != "claimable":
                continue
            if user_id not in exp.get("participants", {}):
                continue
            reward = exp.get("rewards", {}).get(user_id, {})
            if reward and reward.get("claimed"):
                continue
            claimable.append(exp)
        claimable.sort(key=lambda item: item.get("claimable_at", item.get("completed_at", "")), reverse=True)
        return claimable

    def update_expedition_progress(self, expedition_id: str) -> Dict[str, Any]:
        """
        更新科考进度（重新汇总）

        说明：科考贡献已改为“出售鱼类时”写入 participants[*].contribution。
        因此这里不再从钓鱼记录/统计表重算贡献，只做一次汇总（用于定时任务、查看状态、结算前校正）。
        
        Returns:
            更新结果信息
        """
        with self._expedition_lock:
            expeditions = self._load_expeditions()
            if expedition_id not in expeditions:
                return {"success": False, "message": "科考不存在"}

            expedition = expeditions[expedition_id]

            # 重新计算总进度（只汇总已记录的贡献）
            for target_key, target in expedition["targets"].items():
                total_caught = sum(
                    participant["contribution"].get(target_key, 0)
                    for participant in expedition["participants"].values()
                )
                target["caught"] = min(total_caught, target["required"])

            total_caught = sum(t["caught"] for t in expedition["targets"].values())
            total_required = sum(t["required"] for t in expedition["targets"].values())
            expedition["total_progress"] = total_caught / total_required if total_required > 0 else 0

            transition = self._prepare_expedition_claims_locked(expedition)

            expeditions[expedition_id] = expedition
            self._save_expeditions(expeditions)

        if transition.get("status") == "ended":
            self._prune_storage_to_current_and_last()

        logger.info(
            f"科考 {expedition_id} 进度已汇总完成，总进度：{expedition['total_progress']*100:.1f}%"
        )
        if transition.get("status") == "claimable":
            return {"success": True, "message": "科考任务已完成，奖励待领取", "claimable": True}
        return {"success": True, "message": "科考进度已更新"}

    def update_expedition_on_sell_fish(self, user_id: str, sold_fish: Dict[int, int]) -> Dict[str, Any]:
        """
        当用户出售鱼时更新科考进度
        
        Args:
            user_id: 用户ID
            sold_fish: 出售的鱼 {fish_id: quantity}
            
        Returns:
            包含更新信息的字典，如果未更新则返回None
        """
        # 获取用户当前科考
        expedition = self.get_user_expedition(user_id)
        if not expedition:
            return None  # 用户不在科考中，无需更新
        
        expedition_id = expedition["expedition_id"]

        with self._expedition_lock:
            expeditions = self._load_expeditions()
            
            if expedition_id not in expeditions:
                return None
            
            expedition = expeditions[expedition_id]

            if expedition.get("status") != "active":
                return None  # 科考已结束，不再接受进度更新
            
            # 初始化稀有鱼记录
            if "rare_fish_caught" not in expedition:
                expedition["rare_fish_caught"] = {}
            if user_id not in expedition["rare_fish_caught"]:
                expedition["rare_fish_caught"][user_id] = []

            # 构建目标鱼ID映射
            target_fish_ids = {target["fish_id"]: key for key, target in expedition["targets"].items()}

            # 检查出售的鱼中是否有目标鱼
            updated_targets = {}  # 记录更新的目标鱼 {fish_name: {quantity: X, progress: "X/Y"}}
            has_target_update = False
            has_rare_update = False

            for fish_id, quantity in sold_fish.items():
                if not quantity or quantity <= 0:
                    continue

                fish_template = self.item_template_repo.get_fish_by_id(fish_id)
                fish_rarity = getattr(fish_template, "rarity", None)

                # 记录6~10星稀有鱼（用于结算事件池），改为“出售触发”写入
                if fish_rarity is not None and fish_rarity >= 6:
                    expedition["rare_fish_caught"][user_id].extend([fish_id] * quantity)
                    has_rare_update = True

                if fish_id in target_fish_ids:
                    target_key = target_fish_ids[fish_id]
                    current_contribution = expedition["participants"][user_id]["contribution"].get(target_key, 0)
                    expedition["participants"][user_id]["contribution"][target_key] = current_contribution + quantity
                    has_target_update = True

                    fish_name = fish_template.name if fish_template else f"鱼{fish_id}"
                    updated_targets[fish_name] = {
                        "quantity": quantity,
                        "target_key": target_key,
                    }
                    logger.info(f"用户 {user_id} 出售了 {quantity} 条目标鱼 {fish_id}，更新科考贡献")

            if not has_target_update and not has_rare_update:
                return None
            
            # 仅当目标鱼贡献变化时才需要重新计算进度
            if has_target_update:
                for target_key, target in expedition["targets"].items():
                    total_caught = sum(
                        participant["contribution"].get(target_key, 0)
                        for participant in expedition["participants"].values()
                    )
                    target["caught"] = min(total_caught, target["required"])

                total_caught = sum(t["caught"] for t in expedition["targets"].values())
                total_required = sum(t["required"] for t in expedition["targets"].values())
                expedition["total_progress"] = total_caught / total_required if total_required > 0 else 0

            transition = self._prepare_expedition_claims_locked(expedition)

            # 保存更新
            expeditions[expedition_id] = expedition
            self._save_expeditions(expeditions)

        if transition.get("status") == "ended":
            self._prune_storage_to_current_and_last()
        
        # 若没有目标鱼更新，则只记录稀有鱼池，不向外层提示
        if not has_target_update:
            return None

        # 构建返回信息（包含每条鱼的完成进度）
        for fish_name, info in updated_targets.items():
            target_key = info["target_key"]
            target = expedition["targets"][target_key]
            info["progress"] = f"{target['caught']}/{target['required']}"

        logger.info(
            f"科考 {expedition_id} 进度已更新（用户出售鱼触发），总进度：{expedition['total_progress']*100:.1f}%"
        )

        return {
            "updated": True,
            "targets": updated_targets,
            "total_progress": expedition["total_progress"],
            "claimable": transition.get("status") == "claimable",
        }

    def get_expedition_status(self, user_id: str) -> Dict[str, Any]:
        """获取用户当前科考的详细状态"""
        # 加载历史记录
        history = self._load_history()
        user_history = history.get(user_id)
        
        # 获取当前进行中的科考与待领取奖励
        expedition = self.get_user_expedition(user_id)
        claimable_expeditions = self.get_user_claimable_expeditions(user_id)

        # 如果既没有历史记录，也没有进行中科考，也没有待领取奖励
        if not user_history and not expedition and not claimable_expeditions:
            return {"success": False, "message": "你还没有参加过任何科考"}
        
        message_parts = []
        
        # 显示上次科考结算记录
        if user_history:
            message_parts.append("📜 上次科考结算记录")
            message_parts.append("━━━━━━━━━━━━━━━━━━━━")
            message_parts.append(f"🔬 类型：{user_history['expedition_type']}")
            message_parts.append(f"📊 完成度：{user_history['completion_rate'] * 100:.1f}%")
            message_parts.append(f"🎯 贡献：{user_history['contribution']}条")
            message_parts.append(f"💎 钻石奖励：{user_history['premium_reward']}")
            message_parts.append(f"⏰ 结算时间：{user_history['settled_at']}")
        
        if not expedition and claimable_expeditions:
            if user_history:
                message_parts.append("")
                message_parts.append("")
            message_parts.append("")
            message_parts.append("🎁 待领取科考奖励")
            message_parts.append("━━━━━━━━━━━━━━━━━━━━")
            type_names = {"short": "探险", "medium": "征服", "long": "圣域"}
            for claimable in claimable_expeditions[:5]:
                reward = claimable.get("rewards", {}).get(user_id, {})
                message_parts.append(
                    f"  [{claimable.get('expedition_id', '')}] "
                    f"{type_names.get(claimable.get('type', ''), claimable.get('type', ''))}: "
                    f"{self._format_reward_preview(reward)}"
                )
            return {
                "success": True,
                "message": "\n".join(part for part in message_parts if part is not None)
            }

        # 如果当前不在科考中，只返回历史记录
        if not expedition:
            return {
                "success": True,
                "message": "\n".join(message_parts)
            }
        
        # 如果有历史记录，添加分隔符
        if user_history:
            message_parts.append("")
            message_parts.append("")

        expedition_id = expedition["expedition_id"]
        end_time = datetime.strptime(expedition["end_time"], "%Y-%m-%d %H:%M:%S")
        now = get_now()

        # 显示当前科考状态
        message_parts.append(f"🔬 当前科考状态 [{expedition['expedition_id']}]")
        message_parts.append("━━━━━━━━━━━━━━━━━━━━")
        
        # 格式化目标鱼信息
        targets_info = []
        for target in expedition["targets"].values():
            progress_pct = (target["caught"] / target["required"] * 100) if target["required"] > 0 else 0
            bar_length = 10
            filled = int(progress_pct / 10)
            bar = "█" * filled + "░" * (bar_length - filled)
            
            targets_info.append(
                f"  {'⭐' * target['rarity']} {target['fish_name']}: "
                f"{bar} {target['caught']}/{target['required']} ({progress_pct:.0f}%)"
            )

        # 格式化成员贡献
        participants_info = []
        for p in sorted(
            expedition["participants"].values(),
            key=lambda x: sum(x["contribution"].values()),
            reverse=True
        ):
            total_contrib = sum(p["contribution"].values())
            participants_info.append(f"  {p['nickname']}: {total_contrib}条")

        type_names = {"short": "探险", "medium": "征服", "long": "圣域"}
        
        # 计算剩余时间
        remaining = end_time - now
        hours = int(remaining.total_seconds() / 3600)
        minutes = int((remaining.total_seconds() % 3600) / 60)

        message_parts.append(f"📋 类型：{type_names.get(expedition['type'], expedition['type'])}")
        message_parts.append(f"👑 队长：{expedition['creator_name']}")
        message_parts.append(f"👥 成员：{len(expedition['participants'])}人")
        if remaining.total_seconds() >= 0:
            message_parts.append(f"⏰ 剩余时间：{hours}小时{minutes}分钟")
        else:
            message_parts.append("⏰ 已超时：不会自动结算，继续完成全部任务后可领取奖励")
        message_parts.append(f"📊 总进度：{expedition['total_progress'] * 100:.1f}%")
        message_parts.append("")
        message_parts.append("🎯 目标鱼类：")
        message_parts.extend(targets_info)
        message_parts.append("")
        message_parts.append("👤 贡献排行：")
        message_parts.extend(participants_info[:5])
        if claimable_expeditions:
            message_parts.append("")
            message_parts.append("🎁 你还有其他待领取奖励：")
            type_names = {"short": "探险", "medium": "征服", "long": "圣域"}
            for claimable in claimable_expeditions[:3]:
                reward = claimable.get("rewards", {}).get(user_id, {})
                message_parts.append(
                    f"  [{claimable.get('expedition_id', '')}] "
                    f"{type_names.get(claimable.get('type', ''), claimable.get('type', ''))}: "
                    f"{self._format_reward_preview(reward)}"
                )

        return {
            "success": True,
            "message": "\n".join(message_parts)
        }

    def test_complete_expedition(self, user_id: str) -> Dict[str, Any]:
        """测试命令：将当前管理员参与的科考强制按100%完成"""
        with self._expedition_lock:
            expedition = self.get_user_expedition(user_id)
            if not expedition:
                return {"success": False, "message": "你不在任何科考队伍中"}
            
            expedition_id = expedition["expedition_id"]
            expeditions = self._load_expeditions()
            
            if expedition_id not in expeditions:
                return {"success": False, "message": "科考不存在"}
            
            exp = expeditions[expedition_id]
            
            # 将所有目标设置为已完成
            for target_key, target in exp["targets"].items():
                target["caught"] = target["required"]
            
            # 设置总进度为100%
            exp["total_progress"] = 1.0
            
            # 保存修改
            expeditions[expedition_id] = exp
            self._save_expeditions(expeditions)
        
        self.update_expedition_progress(expedition_id)
        logger.info(f"管理员 {user_id} 将科考 {expedition_id} 强制设置为100%完成")

        return {
            "success": True,
            "message": f"✅ 科考 {expedition_id} 已强制设置为100%完成！\n奖励将改为成员各自领取。"
        }

    def end_expedition(self, user_id: str) -> Dict[str, Any]:
        """兼容旧命令：不再手动结算，只给出当前规则提示。"""
        expedition = self.get_user_expedition(user_id)
        if expedition:
            self.update_expedition_progress(expedition["expedition_id"])
            refreshed = self._load_expeditions().get(expedition["expedition_id"], expedition)
            if refreshed.get("status") == "claimable":
                return {
                    "success": True,
                    "message": "科考任务已完成，奖励改为成员各自领取，不再手动结束整队结算。\n"
                               + refreshed.get("settlement_report", "")
                }
            return {
                "success": False,
                "message": "科考不会因超时或手动命令直接结算，请继续完成全部任务后再分别领取奖励。"
            }

        claimable_expeditions = self.get_user_claimable_expeditions(user_id)
        if claimable_expeditions:
            return {
                "success": True,
                "message": "你有待领取的科考奖励。请通过前端对应科考卡片或结算窗口逐个领取。"
            }

        return {"success": False, "message": "你当前没有可结束的科考"}

    def _settle_expedition(self, expedition_id: str, manual: bool = False) -> Dict[str, Any]:
        """兼容旧接口：仅推进到可领取状态，不再直接发奖。"""
        update_result = self.update_expedition_progress(expedition_id)
        expeditions = self._load_expeditions()
        expedition = expeditions.get(expedition_id)
        if not expedition:
            return {"success": False, "message": "科考不存在"}
        if expedition.get("status") == "claimable":
            return {
                "success": True,
                "message": expedition.get("settlement_report", "科考任务已完成，奖励待领取"),
                "rewards": expedition.get("rewards", {}),
            }
        if expedition.get("status") == "ended":
            return {"success": True, "message": expedition.get("settlement_report", "科考已结束")}
        return {
            "success": False,
            "message": update_result.get("message", "科考任务尚未全部完成，暂时不能领取奖励"),
        }

    def claim_expedition_reward(self, user_id: str, expedition_id: str) -> Dict[str, Any]:
        """领取指定科考的个人奖励。"""
        if not expedition_id:
            return {"success": False, "message": "缺少科考ID"}

        self.update_expedition_progress(expedition_id)

        should_prune = False
        with self._expedition_lock:
            expeditions = self._load_expeditions()
            expedition = expeditions.get(expedition_id)
            if not expedition:
                return {"success": False, "message": "科考不存在或已结束"}

            if user_id not in expedition.get("participants", {}):
                return {"success": False, "message": "你不是该科考成员"}

            status = expedition.get("status", "active")
            if status == "active":
                return {"success": False, "message": "科考任务尚未全部完成，暂时不能领取奖励"}

            rewards = expedition.get("rewards", {}) if isinstance(expedition.get("rewards", {}), dict) else {}
            reward = rewards.get(user_id)
            if not isinstance(reward, dict):
                return {"success": True, "already_claimed": True, "message": "你当前没有可领取的科考奖励"}

            if reward.get("claimed"):
                return {
                    "success": True,
                    "already_claimed": True,
                    "message": "你已经领取过该科考奖励了",
                    "reward": reward,
                }

            user = self.user_repo.get_by_id(user_id)
            if not user:
                return {"success": False, "message": "用户不存在"}

            premium = int(reward.get("premium", 0) or 0)
            user.premium_currency += premium
            self.user_repo.update(user)

            now_str = get_now().strftime("%Y-%m-%d %H:%M:%S")
            reward["claimed"] = True
            reward["claimed_at"] = now_str
            rewards[user_id] = reward
            expedition["rewards"] = rewards
            self._record_user_expedition_result(user_id, expedition, reward)

            if all(entry.get("claimed") for entry in rewards.values()):
                expedition["status"] = "ended"
                expedition["ended_at"] = now_str
                should_prune = True

            expeditions[expedition_id] = expedition
            self._save_expeditions(expeditions)

        if should_prune:
            self._prune_storage_to_current_and_last()

        return {
            "success": True,
            "message": f"成功领取科考奖励：{premium}钻石",
            "reward": reward,
            "expedition_status": "ended" if should_prune else "claimable",
        }

    def get_expedition_special_event(self, user_id: str, expedition_id: str) -> Dict[str, Any]:
        """读取指定科考的特殊事件文本。

        仅在科考完成（status 为 claimable/ended）时返回有效文本，否则返回 success=False。
        """
        if not expedition_id:
            return {"success": False, "message": "缺少科考ID"}

        # 触发一次进度推进，必要时把 active 100% 的科考推进到 claimable
        self.update_expedition_progress(expedition_id)

        with self._expedition_lock:
            expeditions = self._load_expeditions()
            expedition = expeditions.get(expedition_id)
            if not isinstance(expedition, dict):
                return {"success": False, "message": "科考不存在或已结束"}

            if user_id and user_id not in expedition.get("participants", {}):
                return {"success": False, "message": "你不是该科考成员"}

            status = expedition.get("status", "active")
            if status == "active":
                return {"success": False, "message": "科考尚未完成"}

            events = expedition.get("special_events", [])
            if not isinstance(events, list):
                events = []

            has_event = bool(events)
            text = "\n".join(events).strip() if has_event else "本次科学考察无异象发生……"

            return {
                "success": True,
                "has_event": has_event,
                "text": text,
            }

    def schedule_active_expeditions(self) -> None:
        """兼容旧启动流程：科考不再安排自动结算。"""
        return None

    def _schedule_settlement(self, expedition_id: str, end_time_str: str) -> None:
        """兼容旧接口：不再安排自动结算。"""
        return None

    def _cancel_settlement_timer(self, expedition_id: str) -> None:
        """取消定时器"""
        with self._expedition_lock:
            timer = self._settle_timers.pop(expedition_id, None)
        if timer:
            try:
                timer.cancel()
            except Exception:
                pass

    def _trigger_rarity_event(self, expedition: Dict[str, Any], rarity: int) -> Optional[str]:
        """触发星级完成事件判定
        
        Args:
            expedition: 科考数据
            rarity: 完成的星级
            
        Returns:
            事件结果文本，如果没有触发事件则返回None
        """
        import random
        
        # 三种事件及其触发率
        events = [
            {"name": "quantum_imaging", "rate": 0.10},  # 量子成像效应
            {"name": "spiritual_evolution", "rate": 0.08},  # 天材地宝
            {"name": "abyss_vortex", "rate": 0.12}  # 深渊漩涡
        ]
        
        # 随机判定是否触发事件
        rand = random.random()
        cumulative_rate = 0
        triggered_event = None
        
        for event in events:
            cumulative_rate += event["rate"]
            if rand < cumulative_rate:
                triggered_event = event["name"]
                break
        
        if not triggered_event:
            return None
        
        # 根据科考类型确定影响人数
        participant_count = {"short": 1, "medium": 2, "long": 3}.get(expedition["type"], 1)
        fish_count = {"short": 1, "medium": 2, "long": 3}.get(expedition["type"], 1)
        
        # 获取参与者列表
        participant_ids = list(expedition["participants"].keys())
        if not participant_ids:
            return None
        
        # 随机选择受影响的成员
        selected_users = random.sample(participant_ids, min(participant_count, len(participant_ids)))
        
        # 执行事件效果
        if triggered_event == "quantum_imaging":
            # ①量子成像效应：随机成员获得其他成员钓起的6~10星鱼
            result_lines = []
            rare_fish_pool = []
            
            # 收集所有成员钓起的稀有鱼
            for user_id in participant_ids:
                if user_id in expedition.get("rare_fish_caught", {}):
                    rare_fish_pool.extend(expedition["rare_fish_caught"][user_id])
            
            if rare_fish_pool:
                for user_id in selected_users:
                    user = self.user_repo.get_by_id(user_id)
                    if user:
                        # 随机选择鱼
                        selected_fish = random.choices(rare_fish_pool, k=min(fish_count, len(rare_fish_pool)))
                        
                        # 添加到用户鱼塘
                        from core.services.aquarium_service import AquariumService
                        aquarium_service = AquariumService(self.user_repo, self.item_template_repo)
                        
                        for fish_id in selected_fish:
                            aquarium_service.add_fish_to_aquarium(user_id, fish_id)
                        
                        nickname = expedition["participants"][user_id]["nickname"]
                        result_lines.append(f"  {nickname} 观测到了{len(selected_fish)}条稀有鱼")
                
                return f"  🌟 量子成像效应！在见到科考同伴的渔获时，产生了量子成像效应：\n" + "\n".join(result_lines)
        
        elif triggered_event == "spiritual_evolution":
            # ②天材地宝：随机成员鱼塘中的鱼全部替换成高品质
            result_lines = []
            
            for user_id in selected_users:
                user = self.user_repo.get_by_id(user_id)
                if user and user.aquarium:
                    from core.services.aquarium_service import AquariumService
                    aquarium_service = AquariumService(self.user_repo, self.item_template_repo)
                    
                    # 将鱼塘中所有鱼的品质提升为"优良"或"完美"
                    improved_count = 0
                    for fish_entry in user.aquarium:
                        if fish_entry.get("quality", "普通") not in ["优良", "完美"]:
                            fish_entry["quality"] = random.choice(["优良", "完美"])
                            improved_count += 1
                    
                    if improved_count > 0:
                        self.user_repo.update(user)
                        nickname = expedition["participants"][user_id]["nickname"]
                        result_lines.append(f"  {nickname} 的鱼塘中{improved_count}条鱼发生了进化")
            
            if result_lines:
                return f"  ✨ 天材地宝！路经天材地宝，此处的鱼被四溢的灵气滋养：\n" + "\n".join(result_lines)
        
        elif triggered_event == "abyss_vortex":
            # ③深渊漩涡：随机成员获得5星鱼
            fish_count_by_type = {"short": 10, "medium": 20, "long": 30}
            total_fish = fish_count_by_type.get(expedition["type"], 10)
            
            result_lines = []
            
            # 获取所有5星鱼的模板
            all_fish = self.item_template_repo.get_all_fish()
            five_star_fish = [f for f in all_fish if f.rarity == 5]
            
            if five_star_fish:
                for user_id in selected_users:
                    user = self.user_repo.get_by_id(user_id)
                    if user:
                        # 随机选择5星鱼
                        selected_fish_ids = [random.choice(five_star_fish).fish_id for _ in range(total_fish)]
                        
                        # 添加到背包
                        for fish_id in selected_fish_ids:
                            self.inventory_repo.add_or_update_item(user_id, fish_id, 1)
                        
                        nickname = expedition["participants"][user_id]["nickname"]
                        result_lines.append(f"  {nickname} 获得了{total_fish}条5星鱼")
                
                return f"  🌀 深渊漩涡！成员跌入了海中心的深渊漩涡，却又在凌晨出现在甲板上：\n" + "\n".join(result_lines)
        
        return None

    def get_all_active_expeditions(self, current_user_id: str = "") -> List[Dict[str, Any]]:
        """获取可展示的科考列表（未完成中 + 已完成待领取）。"""
        active_list = []
        changed = False

        with self._expedition_lock:
            expeditions = self._load_expeditions()

            for exp in expeditions.values():
                if exp.get("status", "active") == "active" and self._is_expedition_completed(exp):
                    self._prepare_expedition_claims_locked(exp)
                    changed = True

                if exp.get("status") not in ("active", "claimable"):
                    continue

                end_time = datetime.strptime(exp["end_time"], "%Y-%m-%d %H:%M:%S")
                now = get_now()
                remaining = end_time - now
                remaining_seconds = max(0, int(remaining.total_seconds()))
                rewards = exp.get("rewards", {}) if isinstance(exp.get("rewards", {}), dict) else {}
                current_reward = rewards.get(current_user_id, {}) if current_user_id else {}
                current_user_claimed = bool(current_reward.get("claimed")) if isinstance(current_reward, dict) else False
                current_user_can_claim = (
                    exp.get("status") == "claimable"
                    and bool(current_user_id)
                    and current_user_id in exp.get("participants", {})
                    and isinstance(current_reward, dict)
                    and not current_user_claimed
                )

                active_list.append({
                    "expedition_id": exp["expedition_id"],
                    "type": exp["type"],
                    "creator_name": exp["creator_name"],
                    "member_count": len(exp["participants"]),
                    "total_progress": exp["total_progress"],
                    "targets": exp["targets"],
                    "participants": exp["participants"],
                    "status": exp.get("status", "active"),
                    "is_completed": self._is_expedition_completed(exp),
                    "current_user_can_claim": current_user_can_claim,
                    "current_user_claimed": current_user_claimed,
                    "current_user_reward": current_reward if isinstance(current_reward, dict) else {},
                    "current_user_reward_text": self._format_reward_preview(current_reward if isinstance(current_reward, dict) else {}),
                    "remaining_hours": int(remaining_seconds / 3600),
                    "remaining_minutes": int((remaining_seconds % 3600) / 60),
                    "is_overtime": remaining.total_seconds() < 0,
                })

            if changed:
                self._save_expeditions(expeditions)

        return active_list

    def auto_settle_expired_expeditions(self) -> int:
        """兼容旧调度入口：科考超时后不再自动结算。"""
        return 0
