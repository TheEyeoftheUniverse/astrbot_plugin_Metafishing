from astrbot.api import logger
from astrbot.core.message.components import At
from typing import Dict, Any
from ..core.services.expedition_service import ExpeditionService


class ExpeditionHandlers:
    """科考命令处理器"""

    def __init__(self, expedition_service: ExpeditionService):
        self.expedition_service = expedition_service

    async def start_expedition(self, plugin, event) -> Dict[str, Any]:
        """
        发起科考
        命令：/发起科考 <探险/征服/圣域> [@用户1 @用户2 ...]
        """
        try:
            msg_text = event.message_str.strip()
            parts = msg_text.split()
            
            if len(parts) < 2:
                return {
                    "success": False,
                    "message": "用法：/发起科考 <探险/征服/圣域> [@用户1 @用户2 ...]\n"
                              "示例：/发起科考 探险\n"
                              "示例：/发起科考 征服 @张三 @李四"
                }
            
            # 解析科考类型
            type_map = {"探险": "short", "征服": "medium", "圣域": "long"}
            exp_type_str = parts[1]
            exp_type = type_map.get(exp_type_str)
            
            if not exp_type:
                return {
                    "success": False,
                    "message": "科考类型错误，请选择：探险、征服或圣域"
                }
            
            # 解析被邀请的用户（从At组件中提取）
            invited_user_ids = []
            message_obj = event.message_obj
            
            # 首先尝试从message_obj中获取At组件（推荐方式）
            if hasattr(message_obj, "message"):
                for comp in message_obj.message:
                    if isinstance(comp, At):
                        # 排除机器人本身的id
                        if hasattr(message_obj, 'self_id') and comp.qq != message_obj.self_id:
                            invited_user_ids.append(plugin._resolve_external_user_id(str(comp.qq), "qq"))
                        elif not hasattr(message_obj, 'self_id'):
                            invited_user_ids.append(plugin._resolve_external_user_id(str(comp.qq), "qq"))
            
            # 如果没有获取到，尝试从原始消息中用正则提取（备用方案）
            if not invited_user_ids:
                import re
                raw_message = event.raw_message if hasattr(event, 'raw_message') else msg_text
                at_pattern = r'\[CQ:at,qq=(\d+)\]'
                matches = re.findall(at_pattern, raw_message)
                if matches:
                    invited_user_ids = [plugin._resolve_external_user_id(match, "qq") for match in matches]
            
            if invited_user_ids:
                logger.info(f"从消息中提取到被邀请用户: {invited_user_ids}")
            
            # 创建科考
            user_id = plugin._get_effective_user_id(event)
            result = self.expedition_service.create_expedition(
                creator_id=user_id,
                expedition_type=exp_type,
                invited_users=invited_user_ids
            )
            
            return result
            
        except Exception as e:
            logger.error(f"发起科考失败: {e}", exc_info=True)
            return {"success": False, "message": f"发起科考失败：{str(e)}"}

    async def join_expedition(self, plugin, event) -> Dict[str, Any]:
        """
        加入科考
        命令：/加入科考 <邀请码>
        """
        try:
            msg_text = event.message_str.strip()
            parts = msg_text.split()
            
            if len(parts) < 2:
                return {
                    "success": False,
                    "message": "用法：/加入科考 <邀请码>\n示例：/加入科考 123456"
                }
            
            expedition_id = parts[1].strip()
            user_id = plugin._get_effective_user_id(event)
            
            result = self.expedition_service.join_expedition(user_id, expedition_id)
            return result
            
        except Exception as e:
            logger.error(f"加入科考失败: {e}", exc_info=True)
            return {"success": False, "message": f"加入科考失败：{str(e)}"}

    async def leave_expedition(self, plugin, event) -> Dict[str, Any]:
        """
        退出科考
        命令：/退出科考
        """
        try:
            user_id = plugin._get_effective_user_id(event)
            result = self.expedition_service.leave_expedition(user_id)
            return result
            
        except Exception as e:
            logger.error(f"退出科考失败: {e}", exc_info=True)
            return {"success": False, "message": f"退出科考失败：{str(e)}"}

    async def expedition_status(self, plugin, event) -> Dict[str, Any]:
        """
        查看科考状态
        命令：/科考状态
        """
        try:
            user_id = plugin._get_effective_user_id(event)
            
            # 先更新当前科考的进度数据
            current_exp = self.expedition_service.get_user_expedition(user_id)
            if current_exp:
                expedition_id = current_exp.get("expedition_id")
                if expedition_id:
                    try:
                        self.expedition_service.update_expedition_progress(expedition_id)
                    except Exception as update_error:
                        logger.warning(f"更新科考进度失败: {update_error}")
            
            result = self.expedition_service.get_expedition_status(user_id)
            return result
            
        except Exception as e:
            logger.error(f"查看科考状态失败: {e}", exc_info=True)
            return {"success": False, "message": f"查看科考状态失败：{str(e)}"}

    async def end_expedition(self, plugin, event) -> Dict[str, Any]:
        """
        查看科考结束/领奖规则
        命令：/结束科考
        """
        try:
            user_id = plugin._get_effective_user_id(event)
            result = self.expedition_service.end_expedition(user_id)
            return result
            
        except Exception as e:
            logger.error(f"结束科考失败: {e}", exc_info=True)
            return {"success": False, "message": f"结束科考失败：{str(e)}"}

    async def expedition_help(self, plugin, event) -> Dict[str, Any]:
        """
        查看科考帮助
        命令：,科考帮助
        """
        help_text = """🔬 科学考察系统帮助

━━━━ 📋 科考类型 ━━━━
🌊 探险（24小时）
    ▸ 队长需要：探险许可证
  ▸ 队员需要：科考通行证
  ▸ 目标：1-3星各100条 | 4星50条 | 5星10条
  ▸ 钻石奖池：1000钻石

⚔️ 征服（48小时）
    ▸ 队长需要：征服许可证
  ▸ 队员需要：科考通行证
  ▸ 目标：1-3星各500条 | 4星100条 | 5星50条
  ▸ 钻石奖池：5000钻石

👑 圣域（72小时）
    ▸ 队长需要：圣域许可证
  ▸ 队员需要：科考通行证
  ▸ 目标：1-3星各1000条 | 4星500条 | 5星100条
  ▸ 钻石奖池：10000钻石

━━━━ 🎮 参与规则 ━━━━
▸ 发起者消耗对应等级的许可证创建科考
▸ 队员加入时消耗一张科考通行证（队长不消耗）
▸ 每个玩家同时只能参与一个科考
▸ 科考不会因为超时自动结算
▸ 全部任务完成后，成员各自领取自己的奖励
▸ 待领取奖励不会阻塞你参与下一场科考

━━━━ 🎯 科考目标 ━━━━
▸ 系统随机选择5种鱼（1-5星各一种）
▸ 队伍成员需要出售指定数量的目标鱼（出售时计入贡献）
▸ 高星级鱼类目标数量较少，降低难度
▸ 进度在出售目标鱼时实时更新

━━━━ 💎 奖励分配 ━━━━
【钻石奖励】按贡献比例分配
  个人钻石 = 钻石奖池 × 完成度 × (个人贡献/总贡献)

━━━━ ✨ 特殊事件 ━━━━
当某个星级完成度达100%时，有概率触发特殊事件。
结算时无论是否触发都会展示，未触发则提示"无异象发生"。

━━━━ 📝 相关命令 ━━━━
,发起科考 <探险/征服/圣域> [@用户]
,加入科考 <邀请码>
,退出科考
,科考状态
,结束科考
,科考帮助

━━━━ ⚠️ 注意事项 ━━━━
▸ 队长不能中途退出未完成中的科考
▸ 中途退出的成员不会获得奖励
▸ 贡献会保留但无法获得结算奖励
▸ 许可证 / 通行证可通过商店或抽奖获得"""
        
        return {"success": True, "message": help_text}

    async def test_expedition(self, plugin, event) -> Dict[str, Any]:
        """
        测试命令：强制将当前科考设置为100%完成
        命令：/测试科考
        """
        user_id = plugin._get_effective_user_id(event)
        result = self.expedition_service.test_complete_expedition(user_id)
        return result
