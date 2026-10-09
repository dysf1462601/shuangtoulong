#!/usr/bin/env python3
"""
run_agent.py - 合并版自动循环执行脚本 (重构精简版)
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
import csv
import datetime
from dotenv import load_dotenv
from openai import OpenAI

# 强制标准输出和错误输出使用 UTF-8，忽略无法转换的字符
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)

# 加载 .env 配置
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(ENV_PATH)


# ==================== 配置管理 ====================
class Config:
    """全局配置类，集中管理所有环境变量和参数"""
    PYTHON_EXEC = os.getenv("PYTHON_EXEC", "").strip() or sys.executable
    MAIN_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    BASE_URL = os.getenv("BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    BASE_URL2 = os.getenv("BASE_URL2", "")
    MAX_STEPS = os.getenv("MAX_STEPS", "20")
    MODEL_NAME = os.getenv("MODEL_NAME", "autoglm-phone")
    DEVICE_ID = os.getenv("DEVICE_ID", "").strip()
    API_KEY = os.getenv("API_KEY", "")
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

    # 历史记录配置
    _history_env = os.getenv("HISTORY_FILE", "").strip()
    HISTORY_FILE = _history_env if _history_env else os.path.join(os.path.dirname(os.path.abspath(__file__)), "comment_history.txt")

    # 运行逻辑参数
    COMMENT_PROBABILITY = float(os.getenv("COMMENT_PROBABILITY", "0.7"))
    PRIVATE_MSG_PROBABILITY = float(os.getenv("PRIVATE_MSG_PROBABILITY", "0.1"))
    SLEEP_MIN = int(os.getenv("SLEEP_MIN", "20"))
    SLEEP_MAX = int(os.getenv("SLEEP_MAX", "60"))
    TASK_TIMEOUT = int(os.getenv("TASK_TIMEOUT", "600"))

    # 业务与评论参数
    SEARCH_KEYWORD = os.getenv("SEARCH_KEYWORD", "AI视频")
    ACCOUNT_NAME = os.getenv("ACCOUNT_NAME", "勾践AI")
    FANS_PERSONA = os.getenv("FANS_PERSONA", "你是一个常年混迹AI视频圈、关注各类UP主的粉丝。")
    COMMENT_THEME = os.getenv("COMMENT_THEME", "老师你好你这AI真的挺厉害，")
    COMMENT_KEYWORDS = os.getenv("COMMENT_KEYWORDS", "老师、AI")
    DEFAULT_COMMENT = os.getenv("DEFAULT_COMMENT", "老师说得对，要做好AI视频，必须选好工具")

    # 任务模式与采集参数
    RUN_MODE = os.getenv("RUN_MODE", "INTERACT").strip().upper()
    _collect_env = os.getenv("COLLECT_FILE", "").strip()
    COLLECT_FILE = _collect_env if _collect_env else os.path.join(os.path.dirname(os.path.abspath(__file__)), "collected_authors.csv")
    COLLECT_APP_NAME = os.getenv("COLLECT_APP_NAME", "火龙漫剧")
    COLLECT_TARGET_ENTITY = os.getenv("COLLECT_TARGET_ENTITY", "作者名称")
    COLLECT_TARGET_PREFIX = os.getenv("COLLECT_TARGET_PREFIX", "作者")
    COLLECT_TARGET_HINT = os.getenv("COLLECT_TARGET_HINT", "作者名通常在视频标题的上方或下方，或者在UP主头像旁边。")
    COLLECT_FILTER = os.getenv("COLLECT_FILTER", "筛选\"最新发布\"和\"未观看\"")
    COLLECT_AI_KEYWORDS = os.getenv("COLLECT_AI_KEYWORDS", "AI视频,AI工具,AIGC,AI绘画,AI生成,人工智能,AI变现")


# ==================== 🌟 任务模板字典 ====================
TASK_TEMPLATES = {
    "NAV_INTERACT": """【任务：切换到下一个视频】
请执行以下操作，做完即结束：
1. 观察你当前在什么页面：
- 如果你在首页、搜索页或其他非播放页面 → 请搜索"{keyword}"，点击【筛选】的功能按钮，并选中"最新发布"和"还未观看"这两个条件，点击第一个视频进入播放页。
- 如果你已经在视频播放页面 → 请向上滑动屏幕 1 次，切换到下一个视频。
- 如果当前是一个私信页面或者评论界面，你要先退出评论私信界面， 返回到视频页面 再滑到下一个视频。
2. 如果上滑后看到的是直播间，请再次上滑跳过。
3. 只要屏幕上显示的是一个新的短视频播放画面，直接宣告任务完成退出。
【安全红线】不要点任何广告、充值、登录。只需换视频，别发评论。""",

    "NAV_COLLECT": os.getenv("NAV_COLLECT_TEMPLATE", """【终极目标：切换到“另一部完全不同的短剧”】
警告：我们的目标是换一部新剧，**绝对不是**播放同一部剧的下一集！
第一步：【严格判断当前页面状态】
🔴 状态1：某部短剧的集数连播界面（带有"第1集"或"第X/XX集"字样）
  必须先返回(Back)，退到信息流页面再上滑！绝对禁止直接上滑。
🔴 状态2：评论区、私信页等
  先返回(Back)然后再上滑屏幕切换。
🔴 状态3：正常的短剧信息流（无明显第X集连播字样）
  直接向上滑动屏幕。
🔴 状态4：首页或搜索页
  搜索"{keyword}"，{collect_filter}，点击第一个视频。
【安全红线】不要点任何支付/广告，仅在【{target_app}】内操作。"""),

    "COLLECT_ACTION": os.getenv("COLLECT_ACTION_TEMPLATE", """【任务：采集当前视频的{collect_target}】
第一步：页面检查
- 选集页面/评论区/直播间 → 退回正常视频播放界面。
- 正常视频界面 → 继续。
第二步：识别目标
- 找到{collect_target}：{collect_hint}
- 思考过程中写出「我识别到的{collect_target}是：XXX」
第三步：结束任务
确认目标后执行 finish。message格式为：COLLECT|{collect_prefix}:加上你在屏幕上实际看到的{collect_target}
【安全红线】不要点赞评论，绝对不要退出【{target_app}】。"""),

    "NAV_DRAMA_COMMENT": """【终极目标：切换到“另一部完全不同的短剧”】
与短剧采集逻辑一致，如果看到集数连播界面，必须先返回主信息流再滑动。
遇到直播跳过。不要点支付/广告。""",

    "ACTION_DRAMA_COMMENT": """【任务：短剧发评论与防重复检测】
1. 点击评论图标打开评论区。
2. 只观察当前屏幕（绝对禁止滑动），找是否有【{account_name}】的评论。
3. 若发现 -> 已经评论过，返回到首页刷新，结束任务。
4. 若没发现 -> 输入：{comment} 发送并结束任务。
【安全红线】绝不点击无关内容，评论区严禁滑动。""",

    "NAV_PROCESS": """【任务：搜索指定作者并进入其视频】
1. 观察当前页面，如果不在首页，请一直Back退回首页。
2. 搜索框搜索作者名称："{author_name}"。
3. 找到最像该作者发布的视频，点击进入。
4. 只要成功进入播放画面，执行 finish 退出。""",

    "NAV_BOSS": """【任务：BOSS直聘页面纠偏与翻页检查】
1. 【全局纠偏】：如在聊天页/详情页 -> 一直Back退回到主页职位列表。
2. 【视觉判断与翻页】：观察职位卡片颜色。
   - 若当前可见的岗位标题全部变灰（已点过），向上滑动屏幕一次加载新岗位，任务完成。
   - 若还有未变灰的新岗位，千万不需要滑动，直接完成任务退出。
【安全红线】只做翻页和恢复，绝对不要点击岗位！""",

    "ACTION_BOSS": """【任务：BOSS直聘自动打招呼与闭环返回】
1. 在职位列表页中找一个未变灰的新岗位，点击进入详情。
2. 若底部是【立即沟通】，点击进入聊天后（系统自动打招呼），立刻点击左上角"<"一直退回到职位列表。
3. 若底部是【继续沟通】或无效岗位，直接退回到职位列表。
【安全红线】必须退回职位列表页才能完成任务，绝对不能停留在聊天框。""",

    "NAV_COLLECT_AI": os.getenv("NAV_COLLECT_AI_TEMPLATE", """【终极目标：在精准搜索流中刷AI视频】
1. 若在首页/非播放页：搜索关键词 "{keyword}"，在分类中点击【视频】Tab，点击第一个视频。
2. 若在搜索视频播放页：直接上滑切换下一个。
3. 遇到直播间直接跳过。进入新短视频即宣告完成。"""),

    "ACTION_COLLECT_AI": os.getenv("ACTION_COLLECT_AI_TEMPLATE", """【任务：判断视频内容并采集AI相关作者】
第一步：判断是否包含领域：{ai_keywords}。
- 若明显无关，直接执行 finish(message="SKIP|非AI视频") 结束。
- 若相关，继续第二步。
第二步：识别并提取作者名称（在思考过程写明）。
第三步：结束任务，message格式必须为：COLLECT|作者:xxx。
【安全红线】就在【{target_app}】操作，不要关注评论。""")
}


# ==================== 设备与历史工具 ====================
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
    if not os.path.exists(Config.HISTORY_FILE): return []
    with open(Config.HISTORY_FILE, "r", encoding="utf-8") as f:
        return [line.strip() for line in f.readlines()][-10:]

def save_history(content):
    with open(Config.HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(content + "\n")

def get_dynamic_comment():
    """根据大模型生成防重复的拟人化评论"""
    history = get_history()
    history_str = "、".join(history) if history else "无"
    prompt = f"""
{Config.FANS_PERSONA}请围绕'{Config.COMMENT_THEME}'这个主题，写一条非常简短、接地气、像真人随手发的评论。
要求：1. 必须包含关键词：{Config.COMMENT_KEYWORDS}；2. 只有一句话，100字以内，不要像硬广，要口语化，意思保持跟我的一致；3. 只输出评论内容，不要引号。
注意：为了保持多样性，请绝对不要重复以下已有的评论内容：[{history_str}]"""
    try:
        client = OpenAI(api_key=Config.DEEPSEEK_API_KEY, base_url=Config.BASE_URL2)
        response = client.chat.completions.create(
            model="GLM-5.3-flash",
            messages=[
                {"role": "system", "content": "你是一个充满活力的社交媒体用户，说话简练。【绝对禁止】输出任何思考过程"},
                {"role": "user", "content": prompt},
            ],
            max_completion_tokens=1024,
            reasoning_effort="low",
            temperature=1.0,
            stream=False
        )
        content = response.choices[0].message.content.strip().replace('"', '').replace('\u201c', '').replace('\u201d', '')
        save_history(content)
        return content
    except:
        return Config.DEFAULT_COMMENT


# ==================== 动态任务构建器 ====================
def generate_nav_task():
    return TASK_TEMPLATES["NAV_INTERACT"].format(keyword=Config.SEARCH_KEYWORD)

def generate_collect_nav_task():
    return TASK_TEMPLATES["NAV_COLLECT"].format(
        keyword=Config.SEARCH_KEYWORD,
        collect_filter=Config.COLLECT_FILTER,
        target_app=Config.COLLECT_APP_NAME
    )

def generate_collect_task():
    return TASK_TEMPLATES["COLLECT_ACTION"].format(
        collect_target=Config.COLLECT_TARGET_ENTITY,
        collect_hint=Config.COLLECT_TARGET_HINT,
        collect_prefix=Config.COLLECT_TARGET_PREFIX,
        target_app=Config.COLLECT_APP_NAME
    )

def generate_action_task():
    roll = random.random()
    if roll < Config.COMMENT_PROBABILITY:
        comment = get_dynamic_comment()
        return f"""【任务：发评论与触底循环检测】
你现在正在看一个视频。请严格按以下逻辑执行：
1. 点击评论图标打开评论区。
2. 【禁止滑动】寻找当前屏幕是否有【{Config.ACCOUNT_NAME}】的评论。
3. 如果发现 -> 重复视频。退回搜索页 -> 重新搜索"{Config.SEARCH_KEYWORD}" -> 筛选最新 -> 点击新视频退出。
4. 如果没发现 -> 输入：{comment} 发送并结束。
【安全红线】绝不点击支付、广告等，绝对禁止滑动评论区。"""
    elif roll < Config.COMMENT_PROBABILITY + Config.PRIVATE_MSG_PROBABILITY:
        comment = get_dynamic_comment()
        return f"""【任务：发私信与触底循环检测】
1. 点击UP主头像进入主页，点击"发私信"。
2. 若已有历史聊天 -> 退回搜索页重新搜索筛选新视频。
3. 若无聊天记录 -> 发送：{comment} 并退回视频页。"""
    else:
        wait_time = random.randint(3, 8)
        return f"""【任务：耐心观看视频】\n请静静观看 {wait_time} 秒后直接退出。不要点赞评论。"""

def generate_process_action_task(author_name):
    roll = random.random()
    if roll < Config.COMMENT_PROBABILITY:
        comment = get_dynamic_comment()
        return f"""【任务：针对特定作者发评论】
1. 对【{author_name}】的视频打开评论区。
2. 【禁止滑动】看是否有【{Config.ACCOUNT_NAME}】的评论。
3. 有 -> 已评论过，退出。 无 -> 评论：{comment} 发送并退出。"""
    elif roll < Config.COMMENT_PROBABILITY + Config.PRIVATE_MSG_PROBABILITY:
        comment = get_dynamic_comment()
        return f"""【任务：针对特定作者发私信】
1. 对【{author_name}】发私信。已有历史记录直接退出，无历史记录发：{comment} 随后退出。"""
    else:
        wait_time = random.randint(3, 8)
        return f"【任务：耐心观看视频】静静观看【{author_name}】的视频 {wait_time} 秒后退出。"


# ==================== 统一执行引擎 ====================
def execute_task(task, capture_output=False):
    """通用执行器，负责调用大模型Agent操作手机，支持是否捕获控制台输出"""
    cmd = [Config.PYTHON_EXEC, Config.MAIN_PY, 
           "--base-url", Config.BASE_URL, 
           "--max-steps", Config.MAX_STEPS,
           "--model", Config.MODEL_NAME, 
           "--device-id", Config.DEVICE_ID, 
           "--apikey", Config.API_KEY, task]
    try:
        if capture_output:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=Config.TASK_TIMEOUT)
            if result.stdout: print(result.stdout)
            if result.stderr: print(result.stderr, file=sys.stderr)
            return result.stdout or ""
        else:
            subprocess.run(cmd, timeout=Config.TASK_TIMEOUT)
            return ""
    except subprocess.TimeoutExpired:
        print(f"\n⏳ [超时警告] main.py 运行超过 {Config.TASK_TIMEOUT} 秒，强制结束进程。")
        return ""
    except KeyboardInterrupt:
        print("\n👋 用户中断，退出。")
        sys.exit(0)


# ==================== 采集与辅助数据处理 ====================
def extract_collect_target_from_output(stdout, collect_prefix, collect_target):
    if not stdout: return None
    try:
        print("  ⚠️ [提示] 启动 LLM 兜底解析思考过程...")
        thinking_text = stdout[-1500:] if len(stdout) > 1500 else stdout
        prompt = f"""从以下AI思考过程中，提取它最终确认的{collect_target}：\n{thinking_text}\n只输出结果本身，不要废话。如果未提到，输出：无法识别"""
        client = OpenAI(api_key=Config.DEEPSEEK_API_KEY, base_url=Config.BASE_URL2)
        response = client.chat.completions.create(
            model="GLM-5.3-flash",
            messages=[{"role": "system", "content": "你是提取助手。只输出结果本身。"}, {"role": "user", "content": prompt}],
            max_completion_tokens=1024,
            reasoning_effort="low",
            temperature=1.0,
            stream=False
        )
        collected_val = response.choices[0].message.content.strip()
        if collected_val and collected_val != "无法识别":
            print(f"  🔍 [大模型提取成功] 解析到{collect_target}: {collected_val}")
            return collected_val
    except Exception as e:
        print(f"  ❌ 提取异常: {e}")
    return None

def save_collected_data(collected_value, target_name):
    existing_data = set()
    if os.path.exists(Config.COLLECT_FILE):
        with open(Config.COLLECT_FILE, 'r', encoding='utf-8') as f:
            for row in csv.reader(f):
                if row: existing_data.add(row[0])

    if collected_value in existing_data:
        print(f"  ⏭️ '{collected_value}' 已存在，跳过保存")
        return False

    file_exists = os.path.exists(Config.COLLECT_FILE)
    with open(Config.COLLECT_FILE, 'a', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([target_name, '采集时间'])
        writer.writerow([collected_value, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
    return True


# ==================== 模块化各模式处理器 ====================
def run_collect_mode():
    print(f"📦 [模式] 采集模式已启动 | 保存至: {Config.COLLECT_FILE}")
    print("=" * 50)
    count = 0
    while True:
        try:
            count += 1
            print(f"\n📍 【第{count}轮 · 导航】滑动找新视频")
            execute_task(generate_collect_nav_task())
            time.sleep(3)
            
            print(f"\n📍 【第{count}轮 · 采集】识别 {Config.COLLECT_TARGET_ENTITY}")
            stdout = execute_task(generate_collect_task(), capture_output=True)
            
            val = extract_collect_target_from_output(stdout, Config.COLLECT_TARGET_PREFIX, Config.COLLECT_TARGET_ENTITY)
            if val and val != "无法识别":
                if save_collected_data(val, Config.COLLECT_TARGET_ENTITY): print(f"\n✅ 采集成功: {val}")
            else:
                print(f"\n⚠️ 采集失败，跳过。")
                
            sleep_t = random.randint(Config.SLEEP_MIN, Config.SLEEP_MAX)
            print(f"⏳ 休眠 {sleep_t} 秒...")
            time.sleep(sleep_t)
        except KeyboardInterrupt: break

def run_drama_comment_mode():
    print(f"📦 [模式] 短剧评论模式已启动")
    print("=" * 50)
    count = 0
    while True:
        try:
            count += 1
            print(f"\n📍 【第{count}轮 · 导航】找新短剧")
            execute_task(TASK_TEMPLATES["NAV_DRAMA_COMMENT"].format(keyword=Config.SEARCH_KEYWORD))
            time.sleep(3)
            
            print(f"\n📍 【第{count}轮 · 评论】")
            execute_task(TASK_TEMPLATES["ACTION_DRAMA_COMMENT"].format(account_name=Config.ACCOUNT_NAME, comment=get_dynamic_comment()))
            
            sleep_t = random.randint(Config.SLEEP_MIN, Config.SLEEP_MAX)
            print(f"⏳ 休眠 {sleep_t} 秒...")
            time.sleep(sleep_t)
        except KeyboardInterrupt: break

def run_process_mode():
    print(f"📦 [模式] 遍历处理模式已启动 | 源文件: {Config.COLLECT_FILE}")
    print("=" * 50)
    while True:
        try:
            if not os.path.exists(Config.COLLECT_FILE):
                print(f"❌ 找不到数据文件，请先运行 COLLECT 模式。")
                break
            with open(Config.COLLECT_FILE, 'r', encoding='utf-8') as f: lines = f.readlines()
            if not lines or (len(lines) == 1 and "采集时间" in lines[0]):
                print(f"✅ 无待处理数据，结束。")
                break
                
            data_idx = 1 if "采集时间" in lines[0] else 0
            if data_idx >= len(lines): break
                
            curr_line = lines[data_idx].strip()
            if not curr_line:
                lines.pop(data_idx)
                with open(Config.COLLECT_FILE, 'w', encoding='utf-8') as f: f.writelines(lines)
                continue
                
            author = curr_line.split(',')[0].strip()
            print(f"\n📍 处理作者：{author}")
            execute_task(TASK_TEMPLATES["NAV_PROCESS"].format(author_name=author))
            time.sleep(3)
            execute_task(generate_process_action_task(author))
            
            print(f"\n✅ '{author}' 处理完成，移除列表。")
            lines.pop(data_idx)
            with open(Config.COLLECT_FILE, 'w', encoding='utf-8') as f: f.writelines(lines)
            
            sleep_t = random.randint(Config.SLEEP_MIN, Config.SLEEP_MAX)
            print(f"⏳ 休眠 {sleep_t} 秒...")
            time.sleep(sleep_t)
        except KeyboardInterrupt: break

def run_collect_ai_mode():
    print(f"🤖 [模式] AI作者采集已启动 | 关键词: {Config.COLLECT_AI_KEYWORDS}")
    count = 0
    while True:
        try:
            count += 1
            print(f"\n📍 【第{count}轮 · 导航】换视频")
            execute_task(TASK_TEMPLATES["NAV_COLLECT_AI"].format(keyword=Config.SEARCH_KEYWORD, collect_filter=Config.COLLECT_FILTER, target_app=Config.COLLECT_APP_NAME))
            time.sleep(3)
            
            print(f"\n📍 【第{count}轮 · 采集】判断与采集")
            stdout = execute_task(TASK_TEMPLATES["ACTION_COLLECT_AI"].format(ai_keywords=Config.COLLECT_AI_KEYWORDS, target_app=Config.COLLECT_APP_NAME), capture_output=True)
            
            if "SKIP" in stdout or "非AI视频" in stdout:
                print(f"\n⏭️ 非目标视频，跳过。")
            else:
                val = extract_collect_target_from_output(stdout, Config.COLLECT_TARGET_PREFIX, Config.COLLECT_TARGET_ENTITY)
                if val and val != "无法识别":
                    if save_collected_data(val, Config.COLLECT_TARGET_ENTITY): print(f"\n✅ 采集成功: {val}")
                    
            sleep_t = random.randint(Config.SLEEP_MIN, Config.SLEEP_MAX)
            print(f"⏳ 休眠 {sleep_t} 秒...")
            time.sleep(sleep_t)
        except KeyboardInterrupt: break

def run_boss_greet_mode():
    print(f"📦 [模式] BOSS直聘自动打招呼启动...")
    count = 0
    while True:
        try:
            count += 1
            print(f"\n📍 【第{count}轮 · 纠偏与找卡片】")
            execute_task(TASK_TEMPLATES["NAV_BOSS"])
            time.sleep(2)
            print(f"\n📍 【第{count}轮 · 打招呼与退回】")
            execute_task(TASK_TEMPLATES["ACTION_BOSS"])
            
            sleep_t = random.randint(Config.SLEEP_MIN, Config.SLEEP_MAX)
            print(f"⏳ 休眠 {sleep_t} 秒...")
            time.sleep(sleep_t)
        except KeyboardInterrupt: break

def run_interact_mode():
    print(f"📦 [模式] 互动模式已启动 (随机评论/私信/浏览)...")
    while True:
        try:
            print(f"\n📍 【导航】找新视频")
            execute_task(generate_nav_task())
            time.sleep(3)
            print(f"\n📍 【操作】评论/私信/浏览")
            execute_task(generate_action_task())
            
            sleep_t = random.randint(Config.SLEEP_MIN, Config.SLEEP_MAX)
            print(f"⏳ 休眠 {sleep_t} 秒...")
            time.sleep(sleep_t)
        except KeyboardInterrupt: break


# ==================== 主入口 ====================
def main():
    print("=" * 50)
    print("  Phone Agent 自动循环脚本 (模块解耦版)")
    print(f"  当前设定模式: {Config.RUN_MODE}")
    print("  按 Ctrl+C 随时退出")
    print("=" * 50)

    if not Config.DEVICE_ID:
        Config.DEVICE_ID = get_device_id()
        if not Config.DEVICE_ID:
            print("\n❌ 未检测到可用的无线设备，退出。")
            sys.exit(1)

    print(f"\n✅ 检测到使用设备: {Config.DEVICE_ID}")
    print("=" * 50)

    # 路由字典分发
    mode_map = {
        "COLLECT": run_collect_mode,
        "DRAMA_COMMENT": run_drama_comment_mode,
        "PROCESS": run_process_mode,
        "COLLECT_AI": run_collect_ai_mode,
        "BOSS_GREET": run_boss_greet_mode,
        "INTERACT": run_interact_mode
    }
    
    runner = mode_map.get(Config.RUN_MODE, run_interact_mode)
    runner()
    print("\n👋 程序退出。Goodbye!")

if __name__ == "__main__":
    main()
