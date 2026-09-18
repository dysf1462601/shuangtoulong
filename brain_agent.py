#!/usr/bin/env python3
"""
brain_agent.py - 通用大脑 (Planner) 驱动 小脑 (main.py) 的长程任务框架
=====================================================================
特性：
  ✅ 无限循环 + 随机休眠（和 run_agent.py 一样）
  ✅ 安全红线（防误触支付/直播/广告，参考 run_agent.py）
  ✅ 状态记忆（历史文本日志，不含历史截图，省 token）
  ✅ 不修改 main.py / phone_agent 任何代码

使用：
  python brain_agent.py "在BOSS直聘中给推荐的岗位逐个打招呼"
  python brain_agent.py --loop "在BOSS直聘中给推荐的岗位逐个打招呼"
  python brain_agent.py   （使用 .env 中的 BRAIN_TASK）
"""

import sys
import io
import os
import re
import time
import json
import random
import subprocess
import traceback
from datetime import datetime
from pathlib import Path

# ==================== 强制 UTF-8 ====================
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)

# ==================== 项目路径 ====================
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from phone_agent.adb.screenshot import get_screenshot
    from phone_agent.device_factory import set_device_type, DeviceType
except ImportError as e:
    print(f"❌ 无法导入 phone_agent: {e}")
    print("   确保 brain_agent.py 与 phone_agent 目录同级")
    sys.exit(1)

try:
    from openai import OpenAI
except ImportError:
    print("❌ 缺少 openai: pip install openai")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass


# ==================== 配置 ====================
class Cfg:
    """所有配置，优先 .env，其次默认值"""

    # ---------- 大脑 ----------
    BRAIN_API_KEY   = os.getenv("BRAIN_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or os.getenv("API_KEY", "")
    BRAIN_BASE_URL  = os.getenv("BRAIN_BASE_URL") or os.getenv("BASE_URL2") or os.getenv("BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    BRAIN_MODEL     = os.getenv("BRAIN_MODEL", "glm-4v-flash")  # ⚠️ 必须是支持视觉的模型
    BRAIN_MAX_TOKEN = int(os.getenv("BRAIN_MAX_TOKENS", "2048"))
    BRAIN_TEMP      = float(os.getenv("BRAIN_TEMPERATURE", "0.2"))

    # ---------- 小脑 (main.py) ----------
    PYTHON_EXEC     = os.getenv("PYTHON_EXEC", "").strip() or sys.executable
    MAIN_PY         = str(PROJECT_ROOT / "main.py")
    SMALL_BASE_URL  = os.getenv("BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    SMALL_MODEL     = os.getenv("MODEL_NAME", "autoglm-phone-9b")
    SMALL_API_KEY   = os.getenv("API_KEY", "")
    SMALL_MAX_STEPS = os.getenv("MAX_STEPS_SUB", "15")
    SMALL_LANG      = os.getenv("LANG", "cn")
    DEVICE_TYPE     = os.getenv("DEVICE_TYPE", "adb")

    # ---------- 设备 ----------
    DEVICE_ID       = os.getenv("DEVICE_ID", "").strip()

    # ---------- 循环 & 休眠（和 run_agent.py 对齐） ----------
    LOOP            = os.getenv("LOOP", "true").strip().lower() in ("1", "true", "yes")
    SLEEP_MIN       = int(os.getenv("SLEEP_MIN", "15"))
    SLEEP_MAX       = int(os.getenv("SLEEP_MAX", "45"))
    MAX_BRAIN_STEPS = int(os.getenv("MAX_BRAIN_STEPS", "200"))   # 单轮最大步数
    SUB_TIMEOUT     = int(os.getenv("TASK_TIMEOUT", "300"))       # 小脑单步超时(秒)
    SCREEN_WAIT     = float(os.getenv("SCREEN_STABLE_WAIT", "2.5"))
    HISTORY_ROUNDS  = int(os.getenv("HISTORY_KEEP_ROUNDS", "15"))
    MAX_ROUND_GOALS = int(os.getenv("MAX_ROUND_GOALS", "20"))     # 每轮最多完成几个小目标

    # ---------- 安全红线关键词（参考 run_agent.py） ----------
    DANGER_KEYWORDS = [
        "支付", "付款", "充值", "购买", "开通会员", "打赏",
        "登录", "注册", "绑定手机号", "授权",
        "直播", "广告", "下载",
    ]

    # ---------- 日志 ----------
    LOG_DIR = PROJECT_ROOT / "brain_logs"
    LOG_DIR.mkdir(exist_ok=True)
    LOG_FILE = str(LOG_DIR / f"brain_{datetime.now():%Y%m%d_%H%M%S}.txt")

    @classmethod
    def validate(cls):
        errs = []
        if not cls.BRAIN_API_KEY:
            errs.append("BRAIN_API_KEY 未配置")
        if not cls.SMALL_API_KEY:
            errs.append("API_KEY 未配置（小脑密钥）")
        if not os.path.isfile(cls.MAIN_PY):
            errs.append(f"找不到 {cls.MAIN_PY}")
        for e in errs:
            print(f"❌ {e}")
        if errs:
            sys.exit(1)


# ==================== 日志 ====================
class Log:
    def __init__(self):
        self._f = open(Cfg.LOG_FILE, "a", encoding="utf-8")

    def p(self, msg, lv="INFO"):
        line = f"[{datetime.now():%H:%M:%S}] [{lv}] {msg}"
        print(line, flush=True)
        self._f.write(line + "\n")
        self._f.flush()

    def close(self):
        self._f.close()


# ==================== 截图 ====================
class Screen:
    def __init__(self):
        self.dev = Cfg.DEVICE_ID or None

    def grab(self, retries=3):
        for i in range(retries):
            try:
                if Cfg.DEVICE_TYPE == "adb":
                    set_device_type(DeviceType.ADB)
                elif Cfg.DEVICE_TYPE == "hdc":
                    set_device_type(DeviceType.HDC)
                else:
                    set_device_type(DeviceType.ADB)
                shot = get_screenshot(device_id=self.dev, timeout=15)
                if shot.base64_data and len(shot.base64_data) > 200:
                    return shot.base64_data
            except Exception as e:
                print(f"  ⚠️ 截图失败({i+1}/{retries}): {e}")
            if i < retries - 1:
                time.sleep(2)
        return None


# ==================== 小脑执行器 ====================
class Executor:
    """通过 subprocess 调用 main.py，和 run_agent.py 完全一致的方式"""

    def __init__(self, log: Log):
        self.log = log

    def run(self, task: str) -> tuple[bool, str]:
        """
        执行一个子任务
        返回 (成功?, 反馈文本)
        """
        cmd = [
            Cfg.PYTHON_EXEC, Cfg.MAIN_PY,
            "--base-url",  Cfg.SMALL_BASE_URL,
            "--model",     Cfg.SMALL_MODEL,
            "--apikey",    Cfg.SMALL_API_KEY,
            "--max-steps", Cfg.SMALL_MAX_STEPS,
            "--lang",      Cfg.SMALL_LANG,
            "--device-type", Cfg.DEVICE_TYPE,
            "--quiet",
        ]
        if Cfg.DEVICE_ID:
            cmd += ["--device-id", Cfg.DEVICE_ID]
        cmd.append(task)

        self.log.p(f"🦾 小脑执行: {task[:120]}...")
        try:
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=Cfg.SUB_TIMEOUT,
                cwd=str(PROJECT_ROOT),
            )
            out = (r.stdout or "") + "\n" + (r.stderr or "")
            fb = self._key_lines(out)
            return (r.returncode == 0), fb

        except subprocess.TimeoutExpired:
            return False, f"子任务超时({Cfg.SUB_TIMEOUT}s)，可能卡死。建议返回或重启APP。"
        except FileNotFoundError:
            return False, f"找不到 {Cfg.PYTHON_EXEC} 或 {Cfg.MAIN_PY}"
        except Exception as e:
            return False, f"执行异常: {e}"

    @staticmethod
    def _key_lines(out: str) -> str:
        """过滤系统检查日志，只保留关键行（和 run_agent.py 的 run_main_capture 类似）"""
        if not out:
            return "（无输出）"
        skip = [
            "Checking system requirements", "Checking ADB", "Checking connected",
            "Checking ADB Keyboard", "Checking model API", "Checking API connectivity",
            "All system checks passed", "Model API checks passed",
            "Phone Agent - AI-powered", "=" * 40, "-" * 40,
            "Model:", "Base URL:", "Max Steps:", "Language:", "Device Type:", "Device:",
        ]
        keep_kw = [
            "Result:", "finish", "✅", "❌", "🎉", "🎯", "💭",
            "Error", "error", "failed", "Failed", "Task completed", "Max steps",
        ]
        lines = []
        for ln in out.split("\n"):
            s = ln.strip()
            if not s:
                continue
            if any(k in s for k in skip):
                continue
            if any(k in s for k in keep_kw):
                lines.append(s)
        if not lines:
            lines = [l.strip() for l in out.split("\n")[-15:] if l.strip()]
        txt = "\n".join(lines)
        return txt[:2000] + ("\n...(截断)" if len(txt) > 2000 else "")


# ==================== 大脑 ====================
class Brain:
    """
    通用多模态大模型作为规划者
    安全红线 + 状态记忆 + 无限循环 全部在这里
    """

    # ---------- System Prompt（融合了 run_agent.py 的安全红线） ----------
    SYSTEM = """你是一个高级手机自动化规划专家（大脑）。你负责完成用户给定的宏观任务。

## 你有一个执行者（小脑）
小脑能听懂自然语言并精确控制手机。你只需要用自然语言描述要做什么，小脑会自己算坐标。
小脑能做的事：
- 打开/启动某个APP（"打开BOSS直聘"）
- 点击某个元素（"点击第一个岗位卡片"、"点击左上角返回按钮"）
- 滑动（"向上滑动一屏"、"向下滑动查看更多"）
- 输入文字（"在搜索框输入xxx"）
- 按返回键（"返回上一页"）
- 回到桌面（"回到手机桌面"）
- 等待（"等待3秒"）
- 长按、双击

## 你的输出格式（必须严格遵守）
每次回复必须包含两部分：
1. 【分析】：简短描述你看到了什么、当前状态、下一步计划
2. 【指令】：给小脑的一个自然语言子任务（只写一个原子操作）

如果你判断宏观任务本轮已完成，输出：
[ROUND_COMPLETED] 加上总结

如果你判断遇到了无法恢复的错误，输出：
[TASK_FAILED] 加上原因

## 🚫 安全红线（必须严格遵守，违反任何一条立即失败）
- 绝对不要点击任何可能触发"支付""付款""充值""购买""开通会员""打赏"的按钮
- 绝对不要点击任何可能触发"登录""注册""绑定手机号""授权"的按钮或弹窗
- 绝对不要进入直播间（有弹幕、礼物特效的页面）
- 绝对不要点击任何广告、推广、下载链接
- 绝对不要执行任何可能导致手机锁屏的操作
- 如果看到弹窗广告或诱导付费，优先关闭它（点X或返回）

## 状态记忆规则
- 你可以在【分析】中记录重要信息（如"已处理岗位：xxx"）
- 这些信息会保留在历史文本中，供你后续步骤参考
- 如果当前页面所有可操作项都已完成（如全部变灰），请滑动到下一页

## 其他规则
- 每次只下达一个原子步骤
- 不要输出坐标，只用自然语言
- 如果上一步操作似乎没生效，先等待2秒再重试，最多重试2次
- 如果连续3次操作都没变化，尝试返回上一级重新进入
"""

    def __init__(self, log: Log):
        self.log = log
        self.client = OpenAI(api_key=Cfg.BRAIN_API_KEY, base_url=Cfg.BRAIN_BASE_URL)
        self.msgs = []
        self.hist = []          # 纯文本历史
        self.round_count = 0    # 当前轮次

    def init_round(self, macro_task: str, screenshot_b64: str):
        """初始化一轮对话"""
        self.round_count += 1
        self.msgs = [
            {"role": "system", "content": self.SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": (
                    f"## 宏观任务（第{self.round_count}轮）\n{macro_task}\n\n"
                    f"## 当前时间\n{datetime.now():%Y-%m-%d %H:%M:%S}\n\n"
                    f"这是手机当前截图。请分析并给出第一步指令。"
                )},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}},
            ]},
        ]
        self.hist = [f"[第{self.round_count}轮开始] 宏观任务: {macro_task[:200]}"]

    def think(self) -> str:
        """调用大脑，返回回复文本"""
        for attempt in range(3):
            try:
                resp = self.client.chat.completions.create(
                    model=Cfg.BRAIN_MODEL,
                    messages=self.msgs,
                    max_tokens=Cfg.BRAIN_MAX_TOKEN,
                    temperature=Cfg.BRAIN_TEMP,
                )
                c = resp.choices[0].message.content
                if c:
                    return c.strip()
            except Exception as e:
                self.log.p(f"大脑API失败({attempt+1}/3): {e}", "ERROR")
                time.sleep((attempt + 1) * 5)
        return "[TASK_FAILED] 大脑API连续失败"

    def feed_result(self, brain_reply, sub_task, ok, feedback, new_b64):
        """把执行结果喂回大脑，管理历史"""
        self.msgs.append({"role": "assistant", "content": brain_reply})

        tag = "✅" if ok else "❌"
        self.hist.append(f"[指令] {sub_task[:200]}")
        self.hist.append(f"[结果] {tag} {feedback[:300]}")

        # 只保留最近 N 轮文本
        mx = Cfg.HISTORY_ROUNDS * 2
        if len(self.hist) > mx:
            self.hist = self.hist[-mx:]

        hist_txt = "\n".join(self.hist[-(Cfg.HISTORY_ROUNDS * 2):])
        content = [
            {"type": "text", "text": (
                f"## 历史（纯文本）\n{hist_txt}\n\n"
                f"## 小脑反馈\n{tag} {feedback[:500]}\n\n"
                f"## 当前最新截图，请决定下一步。"
            )},
        ]
        if new_b64:
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{new_b64}"}})
        else:
            content.append({"type": "text", "text": "（截图失败，请凭文字判断）"})

        self.msgs.append({"role": "user", "content": content})
        self._purge_old_images()

    def _purge_old_images(self):
        """只保留最后一条 user 消息的图片，历史图片全部移除"""
        last_user = -1
        for i in range(len(self.msgs) - 1, -1, -1):
            if self.msgs[i]["role"] == "user":
                last_user = i
                break
        for i, m in enumerate(self.msgs):
            if m["role"] == "user" and isinstance(m.get("content"), list) and i != last_user:
                m["content"] = [
                    it for it in m["content"] if it.get("type") == "text"
                ] or [{"type": "text", "text": "[历史截图已省略]"}]


# ==================== 主控制器 ====================
class Controller:
    def __init__(self, macro_task: str):
        Cfg.validate()
        self.task = macro_task
        self.log = Log()
        self.screen = Screen()
        self.executor = Executor(self.log)
        self.brain = Brain(self.log)
        self._auto_device()

    def _auto_device(self):
        """如果没指定设备，自动检测无线设备（和 run_agent.py 的 get_device_id 一样）"""
        if Cfg.DEVICE_ID:
            self.log.p(f"📱 使用指定设备: {Cfg.DEVICE_ID}")
            return
        try:
            r = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10)
            for line in r.stdout.strip().split("\n")[1:]:
                line = line.strip()
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) == 2 and parts[1].strip() == "device" and ":" in parts[0]:
                    Cfg.DEVICE_ID = parts[0].strip()
                    self.screen.dev = Cfg.DEVICE_ID
                    self.log.p(f"📱 自动检测到设备: {Cfg.DEVICE_ID}")
                    return
        except Exception:
            pass
        self.log.p("⚠️ 未指定设备，将使用默认设备", "WARN")

    # ---------- 单轮执行 ----------
    def run_one_round(self) -> str:
        """
        执行一轮（从截图到 [ROUND_COMPLETED] 或 [TASK_FAILED]）
        返回: "completed" / "failed" / "max_steps"
        """
        self.log.p("=" * 55)
        self.log.p(f"🧠 第 {self.brain.round_count + 1} 轮开始")
        self.log.p(f"📋 宏观任务: {self.task[:150]}")
        self.log.p("=" * 55)

        b64 = self.screen.grab()
        if not b64:
            self.log.p("❌ 初始截图失败", "ERROR")
            return "failed"

        self.brain.init_round(self.task, b64)

        step = 0
        fails = 0
        while step < Cfg.MAX_BRAIN_STEPS:
            step += 1
            self.log.p(f"\n--- 第{step}步 ---")

            # 1) 大脑思考
            reply = self.brain.think()
            self.log.p(f"🧠 {reply[:250]}")

            # 2) 判断是否结束
            if "[ROUND_COMPLETED]" in reply:
                self.log.p("🎉 本轮目标完成！")
                return "completed"
            if "[TASK_FAILED]" in reply:
                self.log.p("❌ 大脑判定失败", "ERROR")
                return "failed"

            # 3) 提取指令
            sub = self._extract_cmd(reply)
            self.log.p(f"📝 子任务: {sub[:150]}")

            # 4) 安全检查（参考 run_agent.py 的安全红线）
            if self._danger_check(sub):
                self.log.p("🚫 安全红线拦截！跳过此指令", "WARN")
                self.brain.feed_result(
                    reply, sub, False,
                    "🚫 该指令涉及危险操作（支付/登录/直播等），已被安全红线拦截。请换一个安全的操作。",
                    None,
                )
                fails += 1
                if fails >= 5:
                    return "failed"
                continue

            # 5) 小脑执行
            ok, fb = self.executor.run(sub)
            if ok:
                fails = 0
            else:
                fails += 1
                self.log.p(f"❌ 小脑失败(连续{fails}次)", "WARN")
            if fails >= 5:
                self.log.p("❌ 连续失败5次，本轮终止", "ERROR")
                return "failed"

            # 6) 等待UI稳定
            time.sleep(Cfg.SCREEN_WAIT)

            # 7) 新截图
            new_b64 = self.screen.grab()

            # 8) 喂回大脑
            self.brain.feed_result(reply, sub, ok, fb, new_b64)

        self.log.p(f"⚠️ 达到单轮最大步数({Cfg.MAX_BRAIN_STEPS})", "WARN")
        return "max_steps"

    # ---------- 无限循环（和 run_agent.py 的 while True 对齐） ----------
    def run_loop(self):
        """
        无限循环模式：
        每轮执行完 → 随机休眠 → 下一轮
        和 run_agent.py 的 while True + time.sleep(random) 完全一致
        """
        total_rounds = 0
        self.log.p("🔁 无限循环模式已启动 (Ctrl+C 退出)")
        self.log.p(f"   休眠范围: {Cfg.SLEEP_MIN}~{Cfg.SLEEP_MAX}秒")

        while True:
            try:
                total_rounds += 1
                self.log.p(f"\n{'='*55}")
                self.log.p(f"🔁 第 {total_rounds} 轮（无限循环）")
                self.log.p(f"{'='*55}")

                result = self.run_one_round()

                if result == "completed":
                    slp = random.randint(Cfg.SLEEP_MIN, Cfg.SLEEP_MAX)
                    self.log.p(f"✅ 本轮完成，随机休眠 {slp} 秒后继续...")
                    time.sleep(slp)
                elif result == "failed":
                    slp = random.randint(Cfg.SLEEP_MIN, Cfg.SLEEP_MAX)
                    self.log.p(f"❌ 本轮失败，休眠 {slp} 秒后重试...", "WARN")
                    time.sleep(slp)
                else:  # max_steps
                    slp = random.randint(Cfg.SLEEP_MIN, Cfg.SLEEP_MAX)
                    self.log.p(f"⚠️ 本轮步数耗尽，休眠 {slp} 秒后继续...", "WARN")
                    time.sleep(slp)

            except KeyboardInterrupt:
                self.log.p(f"\n👋 用户中断，共完成 {total_rounds} 轮。")
                break

    # ---------- 单次模式 ----------
    def run_once(self):
        result = self.run_one_round()
        self.log.p(f"最终结果: {result}")

    # ---------- 工具方法 ----------
    @staticmethod
    def _extract_cmd(reply: str) -> str:
        """从大脑回复中提取指令"""
        # 优先找【指令】标记
        m = re.search(r'【指令】[：:]?\s*(.+)', reply)
        if m:
            return m.group(1).strip()
        # 找包含动作关键词的最后一行
        kws = ["打开", "点击", "滑动", "输入", "返回", "回到", "等待", "长按", "双击", "搜索", "关闭", "退出", "进入", "上滑", "下滑"]
        for line in reversed(reply.strip().split("\n")):
            s = line.strip()
            if s and any(k in s for k in kws):
                return s
        # 兜底：去掉标记后取全文
        cleaned = reply.replace("[ROUND_COMPLETED]", "").replace("[TASK_FAILED]", "").strip()
        return cleaned or reply

    @staticmethod
    def _danger_check(text: str) -> bool:
        """检查指令是否涉及危险操作（参考 run_agent.py 的安全红线）"""
        danger = ["支付", "付款", "充值", "购买", "开通会员", "打赏", "登录", "注册", "绑定手机", "下载APP", "直播间"]
        t = text.lower()
        return any(k in t for k in danger)


# ==================== 入口 ====================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="通用大脑 Agent")
    parser.add_argument("task", nargs="?", default=os.getenv("BRAIN_TASK", ""),
                        help="宏观任务描述")
    parser.add_argument("--loop", action="store_true", help="无限循环模式")
    parser.add_argument("--once", action="store_true", help="只跑一轮")
    args = parser.parse_args()

    macro = args.task
    if not macro:
        macro = os.getenv("BRAIN_TASK", "")
    if not macro:
        macro = input("请输入宏观任务: ").strip()
    if not macro:
        print("❌ 未提供任务")
        sys.exit(1)

    ctrl = Controller(macro)

    if args.once:
        ctrl.run_once()
    elif args.loop or Cfg.LOOP:
        ctrl.run_loop()
    else:
        ctrl.run_once()

    ctrl.log.close()


if __name__ == "__main__":
    main()