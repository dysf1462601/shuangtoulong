#!/usr/bin/env python3
"""
run_agent.py - 合并版自动循环执行脚本 (已泛化任务配置 + 采集逻辑优化 + 遍历处理模式)
安装conda python 3.10的环境
pip install -r requirements.txt 安装依赖
pip install python-dotenv
直接运行: python run_agent.py
"""
import sys
import io
import os
import random
import subprocess
import time
import re                # 用于正则提取
import csv               # 用于CSV写入
import datetime          # 用于记录采集时间
from dotenv import load_dotenv

from openai import OpenAI

# 强制标准输出和错误输出使用 UTF-8，忽略无法转换的字符
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)

# ==================== 加载 .env 配置 ====================
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(ENV_PATH)

# ==================== 基础配置 ====================
PYTHON_EXEC = os.getenv("PYTHON_EXEC", "").strip() or sys.executable
MAIN_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
BASE_URL = os.getenv("BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
BASE_URL2 = os.getenv("BASE_URL2", "")
MAX_STEPS = os.getenv("MAX_STEPS", "20")
MODEL_NAME = os.getenv("MODEL_NAME", "autoglm-phone")
DEVICE_ID = os.getenv("DEVICE_ID", "").strip()
API_KEY = os.getenv("API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

_history_env = os.getenv("HISTORY_FILE", "").strip()
HISTORY_FILE = _history_env if _history_env else os.path.join(os.path.dirname(os.path.abspath(__file__)), "comment_history.txt")

COMMENT_PROBABILITY = float(os.getenv("COMMENT_PROBABILITY", "0.7"))
PRIVATE_MSG_PROBABILITY = float(os.getenv("PRIVATE_MSG_PROBABILITY", "0.1"))
SLEEP_MIN = int(os.getenv("SLEEP_MIN", "20"))
SLEEP_MAX = int(os.getenv("SLEEP_MAX", "60"))
TASK_TIMEOUT = int(os.getenv("TASK_TIMEOUT", "600"))  # 任务超时时间（秒），默认10分钟

# ==================== 业务参数 ====================
SEARCH_KEYWORD = os.getenv("SEARCH_KEYWORD", "AI视频")
ACCOUNT_NAME = os.getenv("ACCOUNT_NAME", "勾践AI")
FANS_PERSONA = os.getenv("FANS_PERSONA", "你是一个常年混迹AI视频圈、关注各类UP主的粉丝。")
COMMENT_THEME = os.getenv("COMMENT_THEME", "老师你好你真的挺厉害，但我觉得想用AI变现，其实不要学，用对一个工具、一个网站、软件就可以了，没这么麻烦的，别被那些套路带偏了")
COMMENT_KEYWORDS = os.getenv("COMMENT_KEYWORDS", "老师、AI变现")
DEFAULT_COMMENT = os.getenv("DEFAULT_COMMENT", "老师说得对，要做好AI视频，必须选好工具")

# ==================== 任务模式配置 ====================
# INTERACT = 互动模式，COLLECT = 采集模式，PROCESS = 遍历处理模式，DRAMA_COMMENT = 短剧评论模式
RUN_MODE = os.getenv("RUN_MODE", "INTERACT").strip().upper()

# 采集模式下采集目标数据的保存文件
_collect_env = os.getenv("COLLECT_FILE", "").strip()
COLLECT_FILE = _collect_env if _collect_env else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "collected_authors.csv"
)

# ==================== 采集模式专用参数 ====================
COLLECT_APP_NAME = os.getenv("COLLECT_APP_NAME", "火龙漫剧")
COLLECT_TARGET_ENTITY = os.getenv("COLLECT_TARGET_ENTITY", "作者名称")
COLLECT_TARGET_PREFIX = os.getenv("COLLECT_TARGET_PREFIX", "作者")
COLLECT_TARGET_HINT = os.getenv("COLLECT_TARGET_HINT", "作者名通常在视频标题的上方或下方，或者在UP主头像旁边。")
COLLECT_FILTER = os.getenv("COLLECT_FILTER", "筛选\"最新发布\"和\"未观看\"")

# ==================== 🌟 任务模板参数化 ====================
TASK_TEMPLATES = {
    # 互动模式：单纯滑动的导航任务
    "NAV_INTERACT": """【任务：切换到下一个视频】
请执行以下操作，做完即结束：
1. 观察你当前在什么页面：
- 如果你在首页、搜索页或其他非播放页面 → 请搜索"{keyword}"，点击【筛选】的功能按钮，并选中"最新发布"和"还未观看"这两个条件，点击第一个视频进入播放页。
- 如果你已经在视频播放页面 → 请向上滑动屏幕 1 次，切换到下一个视频。
- 如果当前是一个私信页面或者评论界面，你要先退出评论私信界面， 返回到视频页面 再滑到下一个视频。
2. 如果上滑后看到的是直播间（有弹幕、礼物特效等），请再次上滑跳过（或者退出直播间再上滑），直到出现正常的短视频。
3. 只要屏幕上显示的是一个新的短视频播放画面，直接宣告任务完成退出。
【安全红线 - 必须严格遵守】
- 🚫 绝对不要点击任何可能触发"支付""付款""充值""购买""开通会员""打赏"的按钮或链接。
- 🚫 绝对不要点击任何可能触发"登录""注册""绑定手机号""授权"的按钮或弹窗。
- 🚫 绝对不要执行任何可能导致手机锁屏的操作。
- 🚫 不要点击任何广告、推广、下载链接。
【注意】本任务只需换到下一个视频，绝对不要做评论或点赞等多余动作。""",

    # 采集模式：跳过选集的导航任务 (已针对"集数播放界面误判"进行深度修复)
    "NAV_COLLECT": os.getenv("NAV_COLLECT_TEMPLATE", """【终极目标：切换到“另一部完全不同的短剧”】
警告：我们的目标是换一部新剧，**绝对不是**播放同一部剧的下一集！

第一步：【严格判断当前页面状态】
仔细观察屏幕上的文字，根据以下规则判断你处于什么页面并执行对应操作：

🔴 状态1：【某部短剧的集数连播界面】（最容易出错的地方！）
【界面特征】：屏幕上能看到具体的当前集数，例如明显包含文字"第1集"、"第2集"、"第X/XX集"、或者带有播放进度和集数控制。
【必须执行】：
  1. 必须先执行 do(action="Back") 返回上一级（也就是回到不同短剧上下滑的信息流页面）。
  2. 返回后再执行上滑（Swipe）切换到下一部短剧。
  **绝对禁止**在这个界面直接上滑，因为那只会切到这部剧的下一集！

🔴 状态2：【评论区、私信页、UP主主页、选集列表】等非视频画面
【必须执行】：先执行 do(action="Back") 返回，然后再向上滑动屏幕1次切换短剧。

🔴 状态3：【正常的短剧信息流界面】
【界面特征】：屏幕上是一个短剧视频，但**绝对没有**具体的"第X集"连播字样（如果底部只写着"选集 · 全XX集"但没有当前第几集，或者画面是连续滑动的信息流，才算此状态）。
【必须执行】：直接向上滑动屏幕1次（Swipe），切换到下一部短剧。

🔴 状态4：【首页、搜索页】
【必须执行】：搜索"{keyword}"，{collect_filter}，点击第一个视频进入播放页。

第二步：【检查滑动或操作后的页面】
- 如果上滑后，你发现仅仅是从"第1集"变成了"第2集"，说明你还在同一部剧里！请立刻执行 do(action="Back") 返回，然后再上滑。
- 如果新页面是【直播间】（有弹幕、礼物等），请再次上滑跳过。
- 只要成功切换到了【新的一部短剧】，直接使用 finish 宣告任务完成。

【安全红线】
- 🚫 绝对不要点击任何支付、登录授权相关按钮或广告。
- 🚫 不要退出当前APP！只在【{target_app}】中操作。"""),

    # 采集模式：核心采集任务
    "COLLECT_ACTION": os.getenv("COLLECT_ACTION_TEMPLATE", """【任务：采集当前视频的{collect_target}】
第一步：【页面检查 - 最关键】
先观察你当前处于什么页面：
- 如果你在【选集页面】或【评论区、私信页、UP主主页】或【直播间】
  → 先点返回回到正常的视频播放页面，然后继续第二步。
- 如果你在【正常的视频播放页面】
  → ✅ 继续第二步。

第二步：【识别{collect_target}】
观察当前视频播放页面，找到{collect_target}：
- {collect_hint}
- 在你的思考过程中，必须明确写出：「我识别到的{collect_target}是：XXX」
- 只记{collect_target}，不要记其他信息。

第三步：【结束任务】
确认{collect_target}后，执行 finish 结束任务。
finish 的 message 格式为：COLLECT|{collect_prefix}:加上你在屏幕上实际看到的{collect_target}
注意：必须填写你在屏幕上实际看到的名称，不要填写其他内容。
如果无法识别{collect_target}，执行 finish(message="COLLECT|{collect_prefix}:无法识别")

⚠️ 不要点赞、不要评论、不要关注、不要点击头像、不要上滑、不要点集数！
绝对不要退出当前APP！就在这个APP中操作 这个APP叫【{target_app}】

【安全红线】不要点击支付、登录授权或广告。"""),

    # 新增：短剧评论模式导航任务
    "NAV_DRAMA_COMMENT": """【终极目标：切换到“另一部完全不同的短剧”】
警告：我们的目标是换一部新剧，**绝对不是**播放同一部剧的下一集！

第一步：【严格判断当前页面状态】
仔细观察屏幕上的文字，根据以下规则判断你处于什么页面并执行对应操作：

🔴 状态1：【某部短剧的集数连播界面】（最容易出错的地方！）
【界面特征】：屏幕上能看到具体的当前集数，特别是顶部、左上角或右上角带有类似"< 第1集"、"< 第2集"等带有返回箭头的集数标志，或者带有播放进度和集数控制。
【必须执行】：
  1. 必须先点击那个带有'<'的返回按钮或者执行 do(action="Back") 返回上一级，退回到不同短剧上下滑的主页/信息流页面。
  2. 返回后再执行上滑（Swipe）切换到下一部短剧。
  **绝对禁止**在这个界面直接上滑，因为那只会切到这部剧的下一集！

🔴 状态2：【评论区、私信页、UP主主页、选集列表】等非视频画面
【必须执行】：先执行 do(action="Back") 返回，然后再向上滑动屏幕1次切换短剧。

🔴 状态3：【正常的短剧信息流界面 / 首页视频流】
【界面特征】：屏幕上是一个短剧视频，但**绝对没有**具体的"第X集"连播字样（如果底部只写着"选集 · 全XX集"但没有当前第几集，或者画面是连续滑动的信息流，才算此状态）。
【必须执行】：直接向上滑动屏幕1次（Swipe），切换到下一部短剧。

🔴 状态4：【非视频的主页界面】
【必须执行】：点击进入视频播放页，或者向上滑动屏幕进入视频流。

第二步：【检查滑动或操作后的页面】
- 如果上滑后，你发现仅仅是从"第1集"变成了"第2集"，说明你还在同一部剧里！请立刻执行 do(action="Back") 返回，然后再上滑。
- 如果新页面是【直播间】（有弹幕、礼物等），请再次上滑跳过。
- 只要成功切换到了【新的一部短剧】的视频播放画面，直接使用 finish 宣告任务完成退出。

【安全红线】
- 🚫 绝对不要点击任何支付、充值、登录授权相关按钮或广告。
- 🚫 不要退出当前APP！""",

    # 新增：短剧评论模式动作任务
    "ACTION_DRAMA_COMMENT": """【任务：短剧发评论与防重复检测】
你现在正在看一个短剧视频。请严格按以下逻辑执行：
1. 点击屏幕右侧或底部的💬评论图标，打开评论区。
2. 【只观察当前屏幕显示的评论即可，绝对禁止在评论列表执行任何滑动(Swipe)操作！】，寻找当前屏幕上是否有【{account_name}】的评论。
3. 【情况A：屏幕上发现了"{account_name}"】 -> 说明我们已经评论过这部短剧，需要看新的。点击"←"或"X"关闭评论区 -> 一直退回到最开始的主页 -> 点击底部导航条左下角的“首页”按钮刷新 -> 宣告任务结束退出。
4. 【情况B：屏幕上没有发现"{account_name}"】 -> 这是新短剧，直接开始评论。点击输入框输入：
{comment}
发送后关闭评论区并宣告任务结束退出。
【安全红线 - 必须严格遵守】
- 🚫 绝不点击支付、充值、登录、广告等无关内容。
- 🚫 在评论区内查找时，绝对禁止执行任何滑动(Swipe)操作！只要第一屏没看到就认定是没有！""",

    # 新增：遍历处理模式导航任务
    "NAV_PROCESS": """【任务：搜索指定作者并进入其视频】
请执行以下操作，做完即结束：
1. 观察你当前在什么页面，如果不在首页或搜索页，请一直执行返回操作（Back），直到回到首页。
2. 在搜索框中搜索作者名称："{author_name}"。
3. 找到最像这个作者发布的视频，或者进入该作者的主页点击他发布的一个视频。
4. 只要成功进入该作者的一个短视频播放画面，直接执行 finish 宣告任务完成退出。
【安全红线】
- 🚫 绝对不要点击任何支付、登录授权相关按钮或广告。
- 🚫 绝对不要执行任何可能导致手机锁屏的操作。
【注意】本任务只需进入目标作者的视频播放页，绝对不要做评论或点赞等多余动作。"""
}

def get_device_id():
    """获取无线设备ID"""
    try:
        result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10)
        lines = result.stdout.strip().split("\n")
        for line in lines[1:]:
            line = line.strip()
            if not line: continue
            parts = line.split("\t")
            if len(parts) != 2: continue
            device_id, status = parts[0].strip(), parts[1].strip()
            if status != "device": continue
            if ":" not in device_id: continue
            print(f"  [发现] 无线设备: {device_id} (状态: {status})")
            return device_id
        return None
    except Exception as e:
        print(f"  [错误] 获取设备列表失败: {e}")
        return None

def get_history():
    if not os.path.exists(HISTORY_FILE): return []
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return [line.strip() for line in f.readlines()][-10:]

def save_history(content):
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(content + "\n")

def get_dynamic_comment():
    history = get_history()
    history_str = "、".join(history) if history else "无"
    prompt = f"""
{FANS_PERSONA}请围绕'{COMMENT_THEME}'这个主题，写一条非常简短、接地气、像真人随手发的评论。
要求：1. 必须包含关键词：{COMMENT_KEYWORDS}；2. 只有一句话，100字以内，不要像硬广，要口语化，意思保持跟我的一致；3. 只输出评论内容，不要引号。
注意：为了保持多样性，请绝对不要重复以下已有的评论内容：[{history_str}]"""
    try:
        # client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=BASE_URL2)
        response = client.chat.completions.create(
            # model="deepseek-chat",
            model="GLM-5.3-flash",
            messages=[
                {"role": "system", "content": "你是一个充满活力的社交媒体用户，说话简练。【绝对禁止】输出任何思考过程"},
                {"role": "user", "content": prompt},
            ],
            # max_tokens=3000, temperature=1.2, stream=False
            # 【优化2】对于推理模型，使用 max_completion_tokens 代替 max_tokens
            max_completion_tokens=1024, 
            # 【优化3】设置思考强度为最低 (可选值: "low", "medium", "high")
            reasoning_effort="low",
            # 【优化4】推理模型通常只支持 temperature=1.0，设置 1.2 可能会在某些网关报错
            temperature=1.0, 
            stream=False
        )
        # print(response)
        content = response.choices[0].message.content.strip().replace('"', '').replace('\u201c', '').replace('\u201d', '')
        save_history(content)
        return content
    except:
        return DEFAULT_COMMENT

# ==================== 任务构建器 ====================
def generate_nav_task():
    return TASK_TEMPLATES["NAV_INTERACT"].format(keyword=SEARCH_KEYWORD)

def generate_collect_nav_task():
    return TASK_TEMPLATES["NAV_COLLECT"].format(
        keyword=SEARCH_KEYWORD,
        collect_filter=COLLECT_FILTER,
        target_app=COLLECT_APP_NAME
    )

def generate_collect_task():
    return TASK_TEMPLATES["COLLECT_ACTION"].format(
        collect_target=COLLECT_TARGET_ENTITY,
        collect_hint=COLLECT_TARGET_HINT,
        collect_prefix=COLLECT_TARGET_PREFIX,
        target_app=COLLECT_APP_NAME
    )



def generate_action_task():
    """互动模式下的动作任务"""
    roll = random.random()
    if roll < COMMENT_PROBABILITY:
        comment = get_dynamic_comment()
        return f"""【任务：发评论与触底循环检测】
你现在正在看一个视频。请严格按以下逻辑执行：
1. 点击右下角💬评论图标，打开评论区。
2. 【只观察当前屏幕显示的评论即可，绝对禁止在评论列表执行任何滑动(Swipe)操作！】，寻找当前屏幕上是否有【{ACCOUNT_NAME}】。
3. 【情况A：屏幕上发现了"{ACCOUNT_NAME}"】 -> 这是视频重复，需要重新搜索新视频。点击"←"关闭评论区 -> 一直退回搜索页 -> 清空并重新搜索"{SEARCH_KEYWORD}" -> 点击【筛选】的功能按钮，并选中"最新发布"和"还未观看"这两个条件， -> 点击第一个视频进入新视频并宣告任务结束。
4. 【情况B：屏幕上没有发现"{ACCOUNT_NAME}"】 -> 这是新视频，直接开始评论。点击输入框输入：
{comment}
发送后关闭评论区并宣告任务结束。
【安全红线 - 必须严格遵守】
- 🚫 绝不点击支付、登录、广告等无关内容。
- 🚫 在评论区内查找时，绝对禁止执行任何滑动(Swipe)操作！只要第一屏没看到就认定是没有！"""
    elif roll < COMMENT_PROBABILITY + PRIVATE_MSG_PROBABILITY:
        comment = get_dynamic_comment()
        return f"""【任务：发私信与触底循环检测】
你现在正在看一个视频。请严格按以下逻辑执行：
1. 点击UP主头像，进入主页，点击"发私信"。
2. 【情况A：已有历史聊天】 -> 视频重复。一直退回搜索页 -> 重新搜索"{SEARCH_KEYWORD}" -> 筛选"最新发布" -> 进入新视频。
3. 【情况B：空白聊天记录】 -> 新UP主。输入并发送：
{comment}
发送后一直退回视频播放页。
【安全红线】绝不点击支付、登录、广告等无关内容。"""
    else:
        wait_time = random.randint(3, 8)
        return f"""【任务：耐心观看视频】
请停留在当前视频，静静观看 {wait_time} 秒，完成后直接退出。
【安全红线】绝不点击支付、登录、广告等无关内容，也不要发评论私信。"""

def generate_drama_nav_task():
    """短剧评论模式下：切换短剧的导航任务"""
    return TASK_TEMPLATES["NAV_DRAMA_COMMENT"].format(keyword=SEARCH_KEYWORD)

def generate_drama_action_task():
    """短剧评论模式下：评论任务"""
    comment = get_dynamic_comment()
    return TASK_TEMPLATES["ACTION_DRAMA_COMMENT"].format(
        account_name=ACCOUNT_NAME,
        keyword=SEARCH_KEYWORD,
        comment=comment
    )

def generate_process_nav_task(author_name):
    """处理模式下：根据作者名称搜索的导航任务"""
    return TASK_TEMPLATES["NAV_PROCESS"].format(author_name=author_name)

def generate_process_action_task(author_name):
    """处理模式下：动作任务（包含防重复执行机制）"""
    roll = random.random()
    if roll < COMMENT_PROBABILITY:
        comment = get_dynamic_comment()
        return f"""【任务：针对特定作者发评论】
你现在正在看一个【{author_name}】的视频。请按以下逻辑执行：
1. 点击右下角💬评论图标，打开评论区。
2. 【只观察当前屏幕显示的评论即可，绝对禁止在评论列表执行任何滑动(Swipe)操作！】，寻找当前屏幕是否有【{ACCOUNT_NAME}】的评论。
3. 【情况A：发现了"{ACCOUNT_NAME}"】 -> 已经评论过。点击关闭评论区 -> 宣告任务完成退出。
4. 【情况B：没有发现"{ACCOUNT_NAME}"】 -> 直接评论。点击输入框输入：
{comment}
发送后关闭评论区，宣告任务完成。
【安全红线 - 必须严格遵守】
- 🚫 绝不点击支付、登录、广告等无关内容。
- 🚫 在评论区内查找时，绝对禁止执行任何滑动(Swipe)操作！只要第一屏没看到就认定是没有！"""
    elif roll < COMMENT_PROBABILITY + PRIVATE_MSG_PROBABILITY:
        comment = get_dynamic_comment()
        return f"""【任务：针对特定作者发私信】
你现在正在看一个【{author_name}】的视频。请按以下逻辑执行：
1. 点击UP主头像，进入主页，点击"发私信"或"私信"。
2. 【情况A：已有历史聊天】 -> 已经私信过。一直退回视频播放页 -> 宣告任务完成退出。
3. 【情况B：空白聊天记录】 -> 新UP主。输入并发送：
{comment}
发送后一直退回视频播放页，宣告任务完成退出。
【安全红线】绝不点击支付、登录、广告等无关内容。"""
    else:
        wait_time = random.randint(3, 8)
        return f"""【任务：耐心观看视频】
请停留在当前【{author_name}】的视频，静静观看 {wait_time} 秒，完成后直接退出。
【安全红线】绝不点击支付、登录、广告等无关内容，也不要发评论私信。"""


# ==================== 执行器 ====================
def run_main(task):
    """运行 main.py (标准输出不捕获)"""
    cmd = [PYTHON_EXEC, MAIN_PY, "--base-url", BASE_URL, "--max-steps", MAX_STEPS,
           "--model", MODEL_NAME, "--device-id", DEVICE_ID, "--apikey", API_KEY, task]
    try:
        subprocess.run(cmd, timeout=TASK_TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f"\n⏳ [超时警告] main.py 运行超过 {TASK_TIMEOUT} 秒，已强制结束进程，准备进入下一轮。")
    except KeyboardInterrupt:
        print("\n用户中断，退出。")
        sys.exit(0)

def run_main_capture(task):
    """运行 main.py 并捕获标准输出用于采集"""
    cmd = [PYTHON_EXEC, MAIN_PY, "--base-url", BASE_URL, "--max-steps", MAX_STEPS,
           "--model", MODEL_NAME, "--device-id", DEVICE_ID, "--apikey", API_KEY, task]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=TASK_TIMEOUT)
        if result.stdout: print(result.stdout)
        if result.stderr: print(result.stderr, file=sys.stderr)
        return result.stdout or ""
    except subprocess.TimeoutExpired:
        print(f"\n⏳ [超时警告] main.py 运行超过 {TASK_TIMEOUT} 秒，已强制结束进程，准备进入下一轮。")
        return ""
    except KeyboardInterrupt:
        print("\n用户中断，退出。")
        sys.exit(0)
        return ""

def extract_collect_target_from_output(stdout, collect_prefix, collect_target):
    """
    优先使用正则直接提取大模型按指令输出的结果，如果失败再兜底使用 DeepSeek 解析
    """
    if not stdout:
        return None

    try:
        
        # 策略2：正则没有找到明确的输出，作为兜底调用 DeepSeek 解析思考过程
        print("  ⚠️ [提示] 未找到明确的 finish message，启动 LLM 兜底解析思考过程...")
        collected_val = extract_collect_target_from_thinking(stdout, collect_target)
        if collected_val and collected_val != "无法识别":
            print(f"  🔍 [大模型提取成功] 解析到{collect_target}: {collected_val}")
            return collected_val

    except Exception as e:
        print(f"  ❌ 提取过程发生异常: {e}")

    return None

def extract_collect_target_from_thinking(stdout, collect_target):
    """兜底解析：用 DeepSeek 分析思考过程"""
    thinking_text = stdout[-1500:] if len(stdout) > 1500 else stdout
    prompt = f"""从以下AI助手的思考过程中，提取它最终识别到的{collect_target}。
思考过程：\n---\n{thinking_text}\n---\n
规则：
1. 找出思考过程中最终确认的{collect_target}
2. 只输出提取到的内容本身，不要输出任何解释、引号或前缀
3. 如果没有明确提到，直接输出：无法识别
提取结果："""
    try:
        # client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=BASE_URL2)
        response = client.chat.completions.create(
            # model="deepseek-chat",
            # model="deepseek-chat",
            model="GLM-5.3-flash",
            messages=[
                {"role": "system", "content": "你是数据提取助手。只输出提取到的结果。【绝对禁止】输出任何思考过程"},
                {"role": "user", "content": prompt},
            ],
            # max_tokens=50, temperature=0, stream=False
            max_completion_tokens=1024, 
            # 【优化3】设置思考强度为最低 (可选值: "low", "medium", "high")
            reasoning_effort="low",
            # 【优化4】推理模型通常只支持 temperature=1.0，设置 1.2 可能会在某些网关报错
            temperature=1.0, 
            stream=False
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"  [DeepSeek 请求失败] {e}")
        return None

def save_collected_data(collected_value, target_name):
    """将采集到的数据追加保存到 CSV 文件（自动去重）"""
    existing_data = set()
    if os.path.exists(COLLECT_FILE):
        with open(COLLECT_FILE, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row in reader:
                if row: existing_data.add(row[0])

    if collected_value in existing_data:
        print(f"  ⏭️ {target_name} '{collected_value}' 已存在，跳过保存")
        return False

    file_exists = os.path.exists(COLLECT_FILE)
    with open(COLLECT_FILE, 'a', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([target_name, '采集时间'])
        writer.writerow([collected_value, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
    return True

# ==================== 主循环 ====================
def main():
    global DEVICE_ID

    print("=" * 50)
    print("  合并版自动循环脚本 (run_agent.py - 泛化任务优化版)")
    print(f"  当前模式: {RUN_MODE}")
    print("  按 Ctrl+C 随时退出")
    print("=" * 50)

    if not DEVICE_ID:
        DEVICE_ID = get_device_id()
        if not DEVICE_ID:
            print("\n❌ 未检测到可用的无线设备，退出。")
            sys.exit(1)

    print(f"\n✅ 使用设备: {DEVICE_ID}")
    print("=" * 50)

    if RUN_MODE == "COLLECT":
        # ==================== 采集模式 ====================
        print(f"📦 采集模式已启动，将不断滑动视频并采集目标 ({COLLECT_TARGET_ENTITY})...")
        print(f"📄 保存文件: {COLLECT_FILE}")
        print("=" * 50)

        collect_count = 0

        while True:
            try:
                collect_count += 1
                print("\n" + "=" * 50)
                print(f"📍 【第{collect_count}轮 · 第1步：导航任务】滑动到新视频")
                print("=" * 50)

                nav_task = generate_collect_nav_task()
                print(f"导航任务内容:\n{nav_task}\n")
                run_main(nav_task)

                print("\n⏳ 导航完成，等待3秒让页面稳定...")
                time.sleep(3)

                print("\n" + "=" * 50)
                print(f"📍 【第{collect_count}轮 · 第2步：采集任务】识别并采集{COLLECT_TARGET_ENTITY}")
                print("=" * 50)

                collect_task = generate_collect_task()
                print(f"采集任务内容:\n{collect_task}\n")
                stdout = run_main_capture(collect_task)

                # 提取 + 保存
                collected_val = extract_collect_target_from_output(stdout, COLLECT_TARGET_PREFIX, COLLECT_TARGET_ENTITY)
                if collected_val and collected_val != "无法识别":
                    saved = save_collected_data(collected_val, COLLECT_TARGET_ENTITY)
                    if saved:
                        print(f"\n✅ 第{collect_count}轮采集成功: {collected_val}")
                else:
                    print(f"\n⚠️ 第{collect_count}轮: 未能采集到{COLLECT_TARGET_ENTITY}，跳过，继续下一轮")

                random_sleep = random.randint(SLEEP_MIN, SLEEP_MAX)
                print(f"\n⏳ 本轮完成，{random_sleep} 秒后开始下一轮采集...")
                time.sleep(random_sleep)

            except KeyboardInterrupt:
                print(f"\n用户中断，程序退出。共完成 {collect_count} 轮采集。Goodbye!")
                break
                
    elif RUN_MODE == "DRAMA_COMMENT":
        # ==================== 短剧评论模式 ====================
        print(f"📦 短剧评论模式已启动，将在短剧APP中不断滑动并评论...")
        print("=" * 50)

        drama_count = 0
        while True:
            try:
                drama_count += 1
                print("\n" + "=" * 50)
                print(f"📍 【第{drama_count}轮 · 第1步：导航任务】切换到新短剧")
                print("=" * 50)
                nav_task = generate_drama_nav_task()
                run_main(nav_task)

                print("\n⏳ 导航完成，等待3秒让页面稳定...")
                time.sleep(3)

                print("\n" + "=" * 50)
                print(f"📍 【第{drama_count}轮 · 第2步：操作任务】对短剧进行评论")
                print("=" * 50)
                action_task = generate_drama_action_task()
                run_main(action_task)

                random_sleep = random.randint(SLEEP_MIN, SLEEP_MAX)
                print(f"\n✅ 本轮全部完成，{random_sleep} 秒后开始下一轮...")
                time.sleep(random_sleep)

            except KeyboardInterrupt:
                print(f"\n用户中断，程序退出。共完成 {drama_count} 轮短剧评论。Goodbye!")
                break
            except Exception as e:
                print(f"\n❌ 处理过程发生错误: {e}")
                time.sleep(5)

    elif RUN_MODE == "PROCESS":
        # ==================== 遍历处理模式 ====================
        print(f"📦 遍历处理模式已启动，将读取 {COLLECT_FILE} 中的作者进行搜索和互动...")
        print("=" * 50)
        
        while True:
            try:
                if not os.path.exists(COLLECT_FILE):
                    print(f"❌ 找不到文件 {COLLECT_FILE}，请先运行 COLLECT 模式采集数据。")
                    break
                
                # 读取CSV
                with open(COLLECT_FILE, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                
                if not lines or (len(lines) == 1 and "采集时间" in lines[0]):
                    print(f"✅ {COLLECT_FILE} 中没有待处理的作者数据，任务结束。")
                    break
                
                # 获取第一条实际数据（如果第一行是表头，则取第二行）
                data_idx = 0
                if "采集时间" in lines[0]:
                    data_idx = 1
                    
                if data_idx >= len(lines):
                    print(f"✅ {COLLECT_FILE} 中所有作者均已处理完毕，任务结束。")
                    break
                    
                current_line = lines[data_idx].strip()
                if not current_line:
                    # 空行清理
                    lines.pop(data_idx)
                    with open(COLLECT_FILE, 'w', encoding='utf-8') as f:
                        f.writelines(lines)
                    continue
                    
                parts = current_line.split(',')
                author_name = parts[0].strip()
                
                print("\n" + "=" * 50)
                print(f"📍 【当前待处理作者】：{author_name}")
                print("=" * 50)
                
                print("\n" + "-" * 40)
                print(f"📍 【第1步：导航任务】搜索作者 '{author_name}' 并进入视频")
                print("-" * 40)
                nav_task = generate_process_nav_task(author_name)
                run_main(nav_task)

                print("\n⏳ 导航完成，等待3秒让页面稳定...")
                time.sleep(3)

                print("\n" + "-" * 40)
                print(f"📍 【第2步：操作任务】对 '{author_name}' 的视频进行评论/私信/浏览")
                print("-" * 40)
                action_task = generate_process_action_task(author_name)
                run_main(action_task)
                
                # 处理完成后，从表格中删除该行并覆写
                print(f"\n✅ 作者 '{author_name}' 互动处理完成，从表格中移除。")
                lines.pop(data_idx)
                with open(COLLECT_FILE, 'w', encoding='utf-8') as f:
                    f.writelines(lines)
                
                random_sleep = random.randint(SLEEP_MIN, SLEEP_MAX)
                print(f"\n⏳ 本轮完成，{random_sleep} 秒后开始处理下一个作者...")
                time.sleep(random_sleep)
                
            except KeyboardInterrupt:
                print("\n用户中断，程序退出。Goodbye!")
                break
            except Exception as e:
                print(f"\n❌ 处理过程发生错误: {e}")
                time.sleep(5)
    else:
        # ==================== 互动模式（保持不变） ====================
        while True:
            try:
                print("\n" + "=" * 50)
                print("📍 【第1步：导航任务】滑动到新视频")
                print("=" * 50)
                nav_task = generate_nav_task()
                run_main(nav_task)

                print("\n⏳ 导航完成，等待3秒让页面稳定...")
                time.sleep(3)

                print("\n" + "=" * 50)
                print("📍 【第2步：操作任务】评论/私信/浏览")
                print("=" * 50)
                action_task = generate_action_task()
                run_main(action_task)

                random_sleep = random.randint(SLEEP_MIN, SLEEP_MAX)
                print(f"\n✅ 本轮全部完成，{random_sleep} 秒后开始下一轮...")
                time.sleep(random_sleep)

            except KeyboardInterrupt:
                print("\n用户中断，程序退出。Goodbye!")
                break

if __name__ == "__main__":
    # print(get_dynamic_comment())
    main()