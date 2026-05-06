import re
import socket
import os
import platform
import signal
import subprocess
import time

import aiohttp
import asyncio

from astrbot.api import logger

async def get_local_ip():
    """异步获取内网IPv4地址"""
    try:
        # 获取本机内网IP地址
        import socket
        # 创建一个socket连接来获取本机IP
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            # 连接到一个外部地址（不会实际发送数据）
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            
        # 验证是否为有效的内网IP地址
        if re.match(r"^(10\.|172\.(1[6-9]|2[0-9]|3[0-1])\.|192\.168\.)", local_ip):
            logger.info(f"获取到内网IP地址: {local_ip}")
            return local_ip
        else:
            logger.warning(f"获取到的IP地址 {local_ip} 不是内网地址，使用localhost")
            return "127.0.0.1"
            
    except Exception as e:
        logger.warning(f"获取内网IP失败: {e}，使用localhost")
        return "127.0.0.1"

async def _is_port_available(port: int) -> bool:
    """异步检查端口是否可用，避免阻塞事件循环"""
    
    def check_sync():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False
            
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, check_sync)
    except Exception as e:
        logger.warning(f"检查端口 {port} 可用性时出错: {e}")
        return False

async def _get_pids_listening_on_port(port: int):
    """返回正在监听指定端口的进程PID列表。"""
    pids = set()
    system_name = platform.system().lower()

    try:
        if "windows" in system_name:
            # Windows: 尝试 netstat
            try:
                process = await asyncio.create_subprocess_exec(
                    "netstat", "-ano",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await process.communicate()
                result = stdout.decode(errors="ignore")
                
                for line in result.splitlines():
                    parts = line.split()
                    if len(parts) >= 5 and parts[0] in ("TCP", "UDP"):
                        local_addr = parts[1]
                        state = parts[3] if parts[0] == "TCP" else "LISTENING"
                        pid = parts[-1]
                        if f":{port}" in local_addr and state.upper() == "LISTENING" and pid.isdigit():
                            pids.add(int(pid))
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
                logger.warning("netstat 不可用或执行失败")
        else:
            # Unix-like: 依次尝试多种方法
            methods = [
                # 方法1: lsof（常见但在容器中可能缺失）
                ("lsof", ["-i", f":{port}", "-sTCP:LISTEN", "-t"]),
                # 方法2: ss（更现代，通常可用）
                ("ss", ["-ltnp", f"sport = {port}"]),
                # 方法3: netstat（传统工具）
                ("netstat", ["-tlnp"])
            ]
            
            for i, (cmd, args) in enumerate(methods):
                try:
                    process = await asyncio.create_subprocess_exec(
                        cmd, *args,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    stdout, _ = await process.communicate()
                    result = stdout.decode(errors="ignore")
                    
                    if i == 0:  # lsof
                        for line in result.splitlines():
                            if line.strip().isdigit():
                                pids.add(int(line.strip()))
                        break
                    elif i == 1:  # ss
                        for line in result.splitlines():
                            if f":{port} " in line or line.strip().endswith(f":{port}"):
                                # 查找 pid=XXXX 或 users:(("进程名",pid=XXXX,fd=X))
                                pid_match = re.search(r'pid=(\d+)', line)
                                if pid_match:
                                    pids.add(int(pid_match.group(1)))
                        break
                    elif i == 2:  # netstat
                        for line in result.splitlines():
                            if f":{port} " in line and "LISTEN" in line:
                                parts = line.split()
                                if len(parts) >= 7 and "/" in parts[-1]:
                                    pid_str = parts[-1].split("/")[0]
                                    if pid_str.isdigit():
                                        pids.add(int(pid_str))
                        break
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
                    continue
            

    except Exception as e:
        logger.warning(f"获取端口 {port} 占用进程时出错: {e}")

    # 排除当前进程，避免误杀自身
    current_pid = os.getpid()
    if current_pid in pids:
        pids.discard(current_pid)
    return list(pids)

async def kill_processes_on_port(port: int):
    """尝试终止监听指定端口的进程。返回 (success, killed_pids)。"""
    pids = await _get_pids_listening_on_port(port)
    if not pids:
        return True, []

    system_name = platform.system().lower()
    killed = []

    for pid in pids:
        try:
            if "windows" in system_name:
                # Windows: 使用 taskkill
                try:
                    process = await asyncio.create_subprocess_exec(
                        "taskkill", "/PID", str(pid), "/F",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    await process.communicate()
                    killed.append(pid)
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    logger.warning(f"taskkill 不可用或超时，尝试直接终止进程 {pid}")
                    # 必要时可尝试其他方法
                    pass
            else:
                # Unix-like: 优雅终止 -> 强制终止
                success = False
                try:
                    os.kill(pid, signal.SIGTERM)
                    # 等待进程响应 SIGTERM
                    for _ in range(10):  # 1秒内检查
                        try:
                            os.kill(pid, 0)  # 检查进程是否存在
                            await asyncio.sleep(0.1)
                        except ProcessLookupError:
                            success = True
                            break
                    
                    if not success:
                        # 进程未响应，强制终止
                        os.kill(pid, signal.SIGKILL)
                    
                    killed.append(pid)
                except ProcessLookupError:
                    # 进程已不存在
                    killed.append(pid)
                except PermissionError:
                    logger.warning(f"权限不足，无法终止进程 {pid}")
                except Exception as e:
                    logger.warning(f"终止进程 {pid} 失败: {e}")
        except Exception as e:
            logger.warning(f"处理进程 {pid} 时出错: {e}")

    # 等待端口释放
    deadline = time.time() + 3
    while time.time() < deadline:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            sock.bind(("0.0.0.0", port))
            sock.close()
            return True, killed
        except Exception:
            await asyncio.sleep(0.2)
            continue

    return len(killed) > 0, killed  # 即使端口未释放，如果杀死了进程也算部分成功

# 将1.2等数字转换成百分数
def _format_percent_number(percent: float) -> str:
    rounded = round(percent, 2)
    if rounded == 0:
        return "0%"
    text = f"{rounded:.2f}".rstrip("0").rstrip(".")
    return f"{text}%"


def to_percentage(value: float) -> str:
    """将小数转换为百分比字符串"""
    if value is None:
        return "0%"
    if value < 1:
        return _format_percent_number(value * 100)
    return _format_percent_number((value - 1) * 100)

def format_rarity_display(rarity: int) -> str:
    """格式化稀有度显示，支持显示到10星，10星以上显示为⭐⭐⭐⭐⭐⭐⭐⭐⭐⭐+"""
    if rarity <= 10:
        return '⭐' * rarity
    else:
        return '⭐⭐⭐⭐⭐⭐⭐⭐⭐⭐+'

def format_accessory_or_rod(accessory_or_rod: dict) -> str:
    """格式化配件信息"""
    # 显示短码而非数字ID
    display_code = accessory_or_rod.get('display_code', f"ID{accessory_or_rod['instance_id']}")
    message =  f" - ID: {display_code}\n"
    message += f" - {accessory_or_rod['name']} (稀有度: {format_rarity_display(accessory_or_rod['rarity'])})\n"
    if accessory_or_rod.get("is_equipped", False):
        message += f"   - {'✅ 已装备'}\n"
    # 显示锁定状态：锁定或未锁定
    if accessory_or_rod.get("is_locked", False):
        message += f"   - {'🔒 已锁定'}\n"
    else:
        message += f"   - {'🔓 未锁定'}\n"
    if accessory_or_rod.get("success_rate_modifier", 0.0) not in (0.0, 0, None):
        message += f"   - 🎯钓鱼成功率加成: {to_percentage(accessory_or_rod['success_rate_modifier'])}\n"
    if accessory_or_rod.get("bonus_fish_quality_modifier", 1.0) != 1.0 and accessory_or_rod.get("bonus_fish_quality_modifier", 1) != 1 and accessory_or_rod.get("bonus_fish_quality_modifier", 1) > 0:
        message += f"   - ✨鱼类品质加成: {to_percentage(accessory_or_rod['bonus_fish_quality_modifier'])}\n"
    if accessory_or_rod.get("bonus_fish_quantity_modifier", 1.0) != 1.0 and accessory_or_rod.get("bonus_fish_quantity_modifier", 1) != 1 and accessory_or_rod.get("bonus_fish_quantity_modifier", 1) > 0:
        message += f"   - 📊鱼类数量加成: {to_percentage(accessory_or_rod['bonus_fish_quantity_modifier'])}\n"
    if accessory_or_rod.get("bonus_rare_fish_chance", 1.0) != 1.0 and accessory_or_rod.get("bonus_rare_fish_chance", 1) != 1 and accessory_or_rod.get("bonus_rare_fish_chance", 1) > 0:
        message += f"   - 🎣稀有鱼概率加成: {to_percentage(accessory_or_rod['bonus_rare_fish_chance'])}\n"
    if accessory_or_rod.get("description"):
        message += f"   - 📋描述: {accessory_or_rod['description']}\n"
    message += "\n"
    return message

from datetime import datetime, timezone, timedelta  # noqa: E402
from typing import Union, Optional, Tuple  # noqa: E402
from astrbot.core.message.components import At  # noqa: E402

def safe_datetime_handler(
    time_input: Union[str, datetime, None],
    output_format: str = "%Y-%m-%d %H:%M:%S",
    default_timezone: Optional[timezone] = None
) -> Union[str, datetime, None]:
    """
    安全处理各种时间格式，支持字符串与datetime互转

    参数:
        time_input: 输入的时间（字符串、datetime对象或None）
        output_format: 输出的时间格式字符串（默认：'%Y-%m-%d %H:%M:%S'）
        default_timezone: 默认时区，如果输入没有时区信息（默认：None）

    返回:
        根据输入类型:
        - 如果输入是字符串: 返回转换后的datetime对象
        - 如果输入是datetime: 返回格式化后的字符串
        - 出错或None: 返回None
    """
    # 处理空输入
    # logger.info(f"Processing time input: {time_input}")
    if time_input is None:
        logger.warning("Received None as time input, returning None.")
        return None

    # 获取默认时区
    if default_timezone is None:
        default_timezone = timezone(timedelta(hours=8))  # 默认东八区

    # 字符串转datetime
    if isinstance(time_input, str):
        try:
            # 尝试ISO格式解析
            dt = datetime.fromisoformat(time_input)
        except ValueError:
            # 尝试常见格式
            formats = [
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%d",
                "%Y/%m/%d %H:%M:%S"
            ]

            for fmt in formats:
                try:
                    dt = datetime.strptime(time_input, fmt)
                    # 添加默认时区
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=default_timezone)
                    break
                except ValueError:
                    continue
            else:
                # 所有格式都失败
                return None
        return dt.strftime(output_format)

    # datetime转字符串
    elif isinstance(time_input, datetime):
        try:
            # 确保有时区信息
            if time_input.tzinfo is None:
                time_input = time_input.replace(tzinfo=default_timezone)
            logger.info(f"Formatting datetime: {time_input}")
            return time_input.strftime(output_format)
        except ValueError as e:
            logger.error(f"Failed to format datetime: {time_input} with error: {e}")
            return None

    logger.error(f"Unsupported time input type: {type(time_input)}")
    # 无法处理的类型
    return None


def sanitize_filename(filename: str) -> str:
    """将字符串转换为安全的文件名，移除或替换特殊字符
    
    Args:
        filename: 原始字符串（可能包含特殊字符）
        
    Returns:
        str: 安全的文件名，特殊字符被替换为下划线
    """
    import re
    # 替换所有非字母数字、下划线、连字符的字符为下划线
    # 保留字母、数字、下划线、连字符和点（用于文件扩展名）
    safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
    # 移除连续的下划线
    safe_name = re.sub(r'_+', '_', safe_name)
    # 移除开头和结尾的下划线
    safe_name = safe_name.strip('_')
    # 如果结果为空，使用默认值
    if not safe_name:
        safe_name = 'unknown'
    return safe_name


def safe_get_file_path(handler_instance, filename: str) -> str:
    """安全生成文件路径，使用处理器的临时目录
    
    Args:
        handler_instance: 处理器实例，需要有 tmp_dir 属性
        filename: 文件名（会自动进行安全化处理）
        
    Returns:
        str: 完整的文件路径
    """
    import os
    # 确保文件名是安全的
    safe_filename = sanitize_filename(filename)
    return os.path.join(handler_instance.tmp_dir, safe_filename)


def parse_target_user_id(event, args: list, arg_index: int = 1) -> Tuple[Optional[str], Optional[str]]:
    """解析目标用户ID，支持用户ID和@两种方式
    
    Args:
        event: 消息事件对象，需要包含 message_obj 属性
        args: 命令参数列表
        arg_index: 用户ID参数在args中的索引位置
        
    Returns:
        tuple: (target_user_id, error_message)
        - target_user_id: 解析出的用户ID，如果解析失败则为None
        - error_message: 错误信息，如果解析成功则为None
        
    Example:
        # 使用@用户方式
        target_id, error = parse_target_user_id(event, ["/修改金币", "@用户", "1000"], 1)
        # 结果: target_id="123456789", error=None
        
        # 使用用户ID方式
        target_id, error = parse_target_user_id(event, ["/修改金币", "123456789", "1000"], 1)
        # 结果: target_id="123456789", error=None
    """
    # 首先尝试从@中获取用户ID
    message_obj = event.message_obj
    target_id = None
    if hasattr(message_obj, "message"):
        # 检查消息中是否有At对象
        for comp in message_obj.message:
            if isinstance(comp, At):
                # 排除机器人本身的id
                if comp.qq != message_obj.self_id:
                    target_id = str(comp.qq)
                    break
    
    # 如果从@中获取到了用户ID，直接返回
    if target_id is not None:
        return str(target_id), None
    
    # 如果没有@，尝试从参数中获取
    if len(args) > arg_index:
        target_user_id = args[arg_index]
        # 接受任意字符串格式的 user_id（支持 QQ 纯数字和钉钉复杂字符串等各种平台）
        return target_user_id, None
    
    # 如果既没有@也没有参数，返回错误
    return None, f"❌ 请指定目标用户（用户ID或@用户），例如：/命令 <用户ID> 或 /命令 @用户"


def parse_amount(amount_str: str) -> int:
    """
    解析用户输入的金额字符串，支持多种写法：
    - 阿拉伯数字，允许逗号分隔："1,000,000" => 1000000
    - 带单位：万/千/百/亿/百万/千万 等（支持混合写法，如 "1千万", "一千三百万", "13百万"）
    - 支持中文数字（零一二三四五六七八九十百千万亿）

    返回整数金额，若解析失败则抛出 ValueError。
    """
    if not isinstance(amount_str, str):
        raise ValueError("amount must be a string")

    s = amount_str.strip()
    if not s:
        raise ValueError("empty amount")

    # 先移除千分位逗号和空白
    s = s.replace(',', '').replace('，', '').replace(' ', '')

    # 快速处理纯数字
    if re.fullmatch(r"\d+", s):
        return int(s)

    # 支持常见带单位的阿拉伯数字，如 1万, 1千万, 13百万
    m = re.fullmatch(r"(?P<num>\d+(?:\.\d+)?)(?P<unit>百万|千万|[万千百亿兆])?", s)
    if m:
        num = float(m.group('num'))
        unit = m.group('unit')
        if not unit:
            return int(num)
        mul_map = {'千': 10**3, '百': 10**2, '万': 10**4, '百万': 10**6, '千万': 10**7, '亿': 10**8, '兆': 10**12}
        mul = mul_map.get(unit, 1)
        return int(num * mul)

    # 将中文数字部分转换为阿拉伯数字（支持混写）
    cn_num_map = {
        '零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
        '六': 6, '七': 7, '八': 8, '九': 9
    }
    unit_map = {'十': 10, '百': 100, '千': 1000, '万': 10**4, '亿': 10**8}

    try:
        total = 0
        section = 0
        number = 0
        i = 0
        s_len = len(s)
        while i < s_len:
            ch = s[i]
            if ch in cn_num_map:
                number = cn_num_map[ch]
                i += 1
            elif ch in unit_map:
                unit_val = unit_map[ch]
                if unit_val >= 10000:
                    section = (section + number) * unit_val
                    total += section
                    section = 0
                else:
                    section += (number if number != 0 else 1) * unit_val
                number = 0
                i += 1
            else:
                # 处理复合单位 '百万','千万'
                if s.startswith('百万', i):
                    section = (section + number) * 10**6
                    total += section
                    section = 0
                    number = 0
                    i += 2
                    continue
                if s.startswith('千万', i):
                    section = (section + number) * 10**7
                    total += section
                    section = 0
                    number = 0
                    i += 2
                    continue
                # 遇到无法识别的字符，抛错
                raise ValueError(f"无法解析的数字字符串: {amount_str}")

        total += section + number
        if total > 0:
            return int(total)
    except ValueError:
        pass

    raise ValueError(f"无法解析的金额: {amount_str}")


def parse_count(count_str: str) -> int:
    """
    解析用户输入的数量字符串，支持多种写法：
    - 阿拉伯数字："5" => 5
    - 中文数字："五" => 5, "十个" => 10, "三个" => 3
    
    返回整数数量，若解析失败则抛出 ValueError。
    """
    if not isinstance(count_str, str):
        raise ValueError("count must be a string")

    s = count_str.strip()
    if not s:
        raise ValueError("empty count")

    # 移除常见量词
    s = s.replace('个', '').replace('只', '').replace('份', '').replace('张', '')
    s = s.replace(' ', '').replace(',', '').replace('，', '')

    # 快速处理纯数字
    if re.fullmatch(r"\d+", s):
        num = int(s)
        if num > 200:
            raise ValueError(f"数量不能超过200: {count_str}")
        return num

    # 中文数字映射
    cn_num_map = {
        '零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
        '六': 6, '七': 7, '八': 8, '九': 9, '十': 10
    }
    
    # 直接匹配单个中文数字
    if s in cn_num_map:
        return cn_num_map[s]
    
    # 处理 "十X" 或 "X十" 的情况
    if s.startswith('十'):
        if len(s) == 1:
            return 10
        if len(s) == 2 and s[1] in cn_num_map:
            return 10 + cn_num_map[s[1]]
    
    if s.endswith('十'):
        if len(s) == 2 and s[0] in cn_num_map:
            return cn_num_map[s[0]] * 10
    
    # 处理 "X十Y" 的情况
    if '十' in s and len(s) == 3:
        parts = s.split('十')
        if len(parts) == 2 and parts[0] in cn_num_map and parts[1] in cn_num_map:
            return cn_num_map[parts[0]] * 10 + cn_num_map[parts[1]]
    
    # 处理更复杂的中文数字（复用 parse_amount 的逻辑，但只支持小数字）
    try:
        # 对于数量，我们限制最大值为200
        result = parse_amount(s)
        if result > 200:
            raise ValueError(f"数量不能超过200: {count_str}")
        return result
    except ValueError:
        pass
    
    raise ValueError(f"无法解析的数量: {count_str}")
