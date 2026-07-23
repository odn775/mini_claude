import sys
import threading
import time
import os
from .agent import run_agent
from .tools import TOOLS, clear_read_cache, execute_tool
from .config import get_config
from .knowledge import build_index, get_index_info
from .skills import list_skills, build_skill_prompt
from .mcp_manager import MCPManager
from .retry import with_retry
from openai import OpenAI


class Style:
    """ANSI 颜色与样式。"""
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    UNDERLINE = "\033[4m"
    REVERSE = "\033[7m"       # 反色（白底黑字）
    REVERSE2 = "\033[7m"      # 同上
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    ORANGE = "\033[38;5;214m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    RED = "\033[91m"
    GRAY = "\033[38;5;244m"
    RESET = "\033[0m"
    CLEAR_LINE = "\033[K"     # 清除到行尾
    CLEAR_DOWN = "\033[J"     # 清除到屏尾
    HIDE_CURSOR = "\033[?25l"
    SHOW_CURSOR = "\033[?25h"

    @staticmethod
    def colored(text: str, color: str, bold: bool = False) -> str:
        """返回带颜色的文本。"""
        return f"{color}{text}{Style.RESET}"

    @staticmethod
    def command(text: str) -> str:
        """命令显示格式（青色+粗体）。"""
        return f"{Style.CYAN}{Style.BOLD}{text}{Style.RESET}"

    @staticmethod
    def highlight(text: str) -> str:
        """高亮显示（黄色）。"""
        return f"{Style.YELLOW}{text}{Style.RESET}"

    @staticmethod
    def muted(text: str) -> str:
        """弱化显示（灰色）。"""
        return f"{Style.GRAY}{text}{Style.RESET}"

    @staticmethod
    def selected(text: str) -> str:
        """选中项（反色）。"""
        return f"{Style.REVERSE} {text} {Style.RESET}"

# ── 知识库路径 ──
_KB_DIR = os.path.join(os.path.expanduser("~"), ".mini_claude", "knowledge")
_KB_INDEX_DIR = os.path.join(os.path.expanduser("~"), ".mini_claude", "faiss_index")

# ── 欢迎画面 ──

DOG = r"""
        /)_/)
       (  •⩊•)  有什么可以帮你的？
       /っ   ﾂ
      /    ﾉﾉ
"""


def thinking_indicator(stop_event: threading.Event):
    """后台线程：每秒更新一次 thinking... 动画。"""
    start = time.time()
    while not stop_event.is_set():
        elapsed = int(time.time() - start)
        sys.stdout.write(f"\r  [thinking] 已思考 {elapsed} 秒")
        sys.stdout.flush()
        time.sleep(1)
    # 清除 thinking 行
    sys.stdout.write("\r" + " " * 60 + "\r")
    sys.stdout.flush()


def _build_system_prompt(context_window: int | None = None) -> str:
    """构建 system prompt，附上 skill 列表供模型自主调用。"""
    skills = list_skills(context_window)
    if not skills:
        return "你是 Mini Claude，一个智能助手。"

    skill_lines = "\n".join(
        f"  /{s['name']}: {s['description']}" for s in skills
    )
    return (
        "你是 Mini Claude，一个智能助手。\n\n"
        "你有以下 skill 可用。当用户的问题匹配某个 skill 时，"
        "调 run_skill(skill_name) 读取完整指令并执行：\n"
        f"{skill_lines}"
    )


# ── 上下文管理 ──

# 常见模型的上下文窗口（token）
_CONTEXT_WINDOWS = {
    "qwen3.7-plus": 1_000_000,
    "qwen3.7-max": 1_000_000,
    "qwen3-plus": 131_072,
    "qwen3-max": 131_072,
}


def _get_context_window(model_name: str) -> int:
    """获取模型的最大上下文窗口。"""
    for prefix, size in _CONTEXT_WINDOWS.items():
        if model_name.startswith(prefix):
            return size
    return 200_000  # 默认


def _estimate_tokens(messages: list[dict]) -> tuple[int, dict[str, int]]:
    """粗略估算当前上下文的 token 数（中文 1 token/字，英文 0.25 token/字符）。

    统计每条消息的 content 文本，以及 assistant 消息中 tool_calls 的
    函数名 + arguments JSON（这部分在 content 之外，容易被漏算）。
    """
    total = 0
    counts = {"system": 0, "user": 0, "assistant": 0, "tool": 0}
    for m in messages:
        content = m.get("content") or ""
        if isinstance(content, list):
            texts = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    texts.append(c.get("text", ""))
            content = " ".join(texts)
        # assistant 的 tool_calls（函数名 + arguments）不在 content 里，单独计入
        extra = ""
        if m.get("tool_calls"):
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                extra += fn.get("name", "") + " " + fn.get("arguments", "")
        text = content + " " + extra
        cn = sum(1 for c in text if "一" <= c <= "鿿")
        other = len(text) - cn
        tokens = int(cn * 1.0 + other * 0.25) + 5  # +5 消息头开销
        total += tokens
        role = m.get("role", "unknown")
        if role in counts:
            counts[role] += tokens
    return total, counts


def _compact_messages(
    messages: list[dict], config: dict, preserve_last_user: bool = False
) -> tuple[list[dict], str]:
    """用 LLM 压缩对话历史，返回新消息列表 + 摘要文本。

    preserve_last_user=True 时（自动压缩用），把最后一条 user 消息原样保留，
    只压缩 system 与该消息之间的历史——避免把"刚问的当前问题"压成摘要。
    """
    # 分离 system prompt
    system = None
    history = messages
    if messages and messages[0].get("role") == "system":
        system = messages[0]
        history = messages[1:]

    # 可选：保留最后一条 user 消息原文
    last_user = None
    if preserve_last_user and history and history[-1].get("role") == "user":
        last_user = history[-1]
        history = history[:-1]

    if not history:
        # 没有可压缩的历史，原样返回
        out = []
        if system:
            out.append(system)
        if last_user:
            out.append(last_user)
        return out, ""

    # 历史 → 纯文本
    history_lines = []
    for m in history:
        role = m["role"]
        content = m.get("content") or ""

        # 处理 tool_calls（assistant 消息 content 可能为空）
        if not content and m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                calls.append(f"-> 调用工具: {fn.get('name', '?')}({fn.get('arguments', '')})")
            content = "\n".join(calls)

        # 处理 content 为 list 的情况
        if isinstance(content, list):
            texts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    texts.append(part.get("text", ""))
            content = "\n".join(texts)

        if len(content) > 3000:
            content = content[:3000] + "\n...(截断)"
        history_lines.append(f"--- [{role}] ---\n{content}\n")

    prompt = (
        "你是一个对话摘要助手。请将以下 AI 助手与用户的对话历史压缩为一段连贯的摘要。\n\n"
        "要求：\n"
        "1. 保留所有用户需求、问题、已做出的决策和已发现的事实\n"
        "2. 保留关键信息（路径、配置、技术选型、错误信息等）\n"
        "3. 保留工具执行的关键结果\n"
        "4. 摘要应足够详细，让 AI 能根据摘要继续准确回答用户问题\n"
        "5. 用中文输出\n\n"
        "对话历史：\n" + "\n".join(history_lines)
    )

    client = OpenAI(api_key=config["api_key"], base_url=config["base_url"])
    resp = with_retry(lambda: client.chat.completions.create(
        model=config["model"],
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2048,
        temperature=0.3,
    ))
    summary = resp.choices[0].message.content or ""

    new_messages = []
    if system:
        new_messages.append(system)
    new_messages.append({
        "role": "user",
        "content": f"（对话历史摘要，请基于此继续回答）\n\n{summary}",
    })
    if last_user:
        new_messages.append(last_user)

    return new_messages, summary


def _maybe_auto_compact(messages: list[dict], config: dict, ctx_window: int) -> bool:
    """run_agent 前调用。token 超过 ctx_window 90% 则自动压缩。

    压缩失败时回退到丢弃最早的历史消息（保留 system + 最后一条 user），
    直到估算 token 降到 70% 以下。返回是否真的做了压缩。
    """
    threshold = int(ctx_window * 0.90)
    tokens, _ = _estimate_tokens(messages)
    if tokens <= threshold:
        return False

    stop = threading.Event()
    spinner = threading.Thread(target=thinking_indicator, args=(stop,), daemon=True)
    spinner.start()
    try:
        new_messages, _ = _compact_messages(messages, config, preserve_last_user=True)
        messages.clear()
        messages.extend(new_messages)
        new_tokens, _ = _estimate_tokens(messages)
        # 摘要后仍超限 → 继续丢最早历史
        drop_threshold = int(ctx_window * 0.70)
        while new_tokens > drop_threshold and len(messages) > 2:
            # 丢掉 system 之后最早的一条
            messages.pop(1)
            new_tokens, _ = _estimate_tokens(messages)
        print(f"{Style.muted('  [自动压缩对话历史]')} "
              f"{Style.muted(f'{tokens:,} → {new_tokens:,} tokens')}")
        return True
    except Exception as e:
        # 压缩失败：回退到丢弃最早历史
        drop_threshold = int(ctx_window * 0.70)
        while tokens > drop_threshold and len(messages) > 2:
            messages.pop(1)
            tokens, _ = _estimate_tokens(messages)
        print(f"{Style.colored(f'  [自动压缩失败: {e}，已丢弃早期历史]', Style.YELLOW)}")
        return True
    finally:
        stop.set()
        spinner.join(timeout=0.5)


def _interactive_skill_picker(skills: list[dict]) -> str | None:
    """方向键选择 skill，回车执行。返回选中 skill 的 name，取消返回 None。"""
    try:
        import msvcrt
    except ImportError:
        # 非 Windows 回退到数字选择
        print(f"\n  {Style.command('可用 skill')} (输入数字选择, 0 取消):")
        for i, s in enumerate(skills, 1):
            print(f"  {Style.highlight(str(i))}. {s['name']:<15} {Style.muted(s['description'])}")
        while True:
            try:
                choice = input(f"\n  {Style.command('?')} 输入编号: ").strip()
                if choice == "0":
                    return None
                idx = int(choice) - 1
                if 0 <= idx < len(skills):
                    return skills[idx]["name"]
                print(f"  {Style.colored('无效编号', Style.RED)}")
            except ValueError:
                print(f"  {Style.colored('请输入数字', Style.RED)}")

    n = len(skills)
    selected = 0
    # 显示区域: 空行 + 标题 + 分隔线 + n 个 skill
    total_lines = 1 + 1 + 1 + n

    # 隐藏光标
    print(Style.HIDE_CURSOR, end="")

    def redraw():
        """从当前位置向上覆盖重绘所有行。"""
        print(f"\033[{total_lines}A", end="")
        sys.stdout.flush()
        lines = []
        lines.append(f"\r{Style.CLEAR_LINE}")
        lines.append(f"\r{Style.CLEAR_LINE}  {Style.command('可用 skill')}  "
                     f"{Style.muted('(↑↓ 选择, Enter 执行, q 取消)')}")
        lines.append(f"\r{Style.CLEAR_LINE}  {Style.muted('─' * 45)}")
        for i, s in enumerate(skills):
            if i == selected:
                lines.append(f"\r{Style.CLEAR_LINE}  {Style.REVERSE} > {s['name']:<15} "
                             f"{s['description'][:40]}  {Style.RESET}")
            else:
                lines.append(f"\r{Style.CLEAR_LINE}    {s['name']:<15} "
                             f"{Style.muted(s['description'][:40])}")
        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.flush()

    def clear_display():
        """清除选择区域。"""
        print(f"\033[{total_lines}A{Style.CLEAR_DOWN}", end="")
        print(Style.SHOW_CURSOR, end="")
        sys.stdout.flush()

    # 初次绘制
    print()
    redraw()

    while True:
        key = msvcrt.getch()
        if key == b"\xe0":  # 方向键前缀
            key2 = msvcrt.getch()
            if key2 == b"H":  # ↑
                selected = max(0, selected - 1)
            elif key2 == b"P":  # ↓
                selected = min(n - 1, selected + 1)
            else:
                continue
            redraw()
        elif key == b"\r":  # Enter
            clear_display()
            return skills[selected]["name"]
        elif key in (b"q", b"Q", b"\x1b"):  # q / Esc
            clear_display()
            return None


def main():
    # ── 命令输入历史 ──
    _input_history: list[str] = []

    # ── 命令补全列表（启动时构建一次） ──
    def _build_command_list() -> list[dict]:
        cmds = [
            ("/exit", "退出"),
            ("/clear", "清空对话历史"),
            ("/context", "查看上下文使用情况"),
            ("/compact", "压缩对话历史"),
            ("/tools", "列出可用工具"),
            ("/skills", "交互式选择 skill"),
            ("/kb rebuild", "重建知识库索引"),
            ("/kb status", "查看知识库状态"),
        ]
        try:
            for s in list_skills():
                cmds.append((f"/{s['name']}", s["description"]))
        except Exception:
            pass
        return [{"cmd": c, "desc": d} for c, d in sorted(cmds)]

    def _get_suggestions(prefix: str, all_cmds: list[dict]) -> list[dict]:
        """根据输入前缀过滤可用命令列表。"""
        if not prefix:
            return []
        return [c for c in all_cmds if c["cmd"].startswith(prefix)]

    def _input_with_completion(all_cmds: list[dict]) -> str:
        """带下拉建议的终端输入。方向键选择，Tab 补全，Esc 关闭。"""
        try:
            import msvcrt
        except ImportError:
            return input("\n> ").strip()

        import shutil

        def _display_width(s: str) -> int:
            """计算字符串在终端中的显示宽度（CJK 宽字符计 2，其余计 1）。"""
            w = 0
            for ch in s:
                cp = ord(ch)
                if (0x4E00 <= cp <= 0x9FFF or      # CJK 统一表意文字
                    0x3400 <= cp <= 0x4DBF or      # CJK 扩展 A
                    0xF900 <= cp <= 0xFAFF or      # CJK 兼容表意文字
                    0xFF01 <= cp <= 0xFF60 or      # 全角形式
                    0x3000 <= cp <= 0x303F or      # CJK 符号和标点
                    0x2E80 <= cp <= 0x2EFF or      # CJK 部首
                    0x20000 <= cp <= 0x2FFFF or    # CJK 扩展 B/C/D/E/F
                    0xFE30 <= cp <= 0xFE4F or      # CJK 兼容形式
                    0x1100 <= cp <= 0x115F or      # 谚文
                    0xAC00 <= cp <= 0xD7AF):       # 谚文音节
                    w += 2
                else:
                    w += 1
            return w

        buffer = ""
        cursor_pos = 0  # 当前光标在 buffer 中的位置
        selected = 0
        suggestions: list[dict] = []
        history_pos = -1
        cursor_line = 0  # 当前光标在提示行之下的行数（下拉框高度）

        def redraw():
            """重绘输入行 + 下拉建议。"""
            nonlocal cursor_line
            # 光标已在输入行 → 清除输入行及下方所有内容，再重绘
            sys.stdout.write(f"[0G[K> {buffer}[J")

            if suggestions:
                # 画新下拉框
                shown = suggestions[:10]
                new_lines = 1 + 1 + len(shown) + 1  # 空行 + 分隔线 + 项 + 分隔线

                width = min(55, shutil.get_terminal_size().columns - 2)
                sep = f"[38;5;244m{'─' * width}[0m"

                sys.stdout.write("[B[0G[K")  # 空行
                sys.stdout.write("[B[0G[K" + sep)

                for i, s in enumerate(shown):
                    cmd = s["cmd"]
                    desc = s["desc"]
                    rest = max(0, width - len(cmd) - 4)
                    desc_d = desc[:rest] if rest > 3 else ""
                    line = f"  {'>' if i == selected else ' '} {cmd}  [38;5;244m{desc_d}[0m"
                    if i == selected:
                        line = f"[7m{line}[0m"
                    sys.stdout.write("[B[0G[K" + line)

                sys.stdout.write("[B[0G[K" + sep)
                cursor_line = new_lines
            else:
                cursor_line = 0

            # 将光标定位到 buffer 中的正确位置
            if cursor_line > 0:
                sys.stdout.write(f"[{cursor_line}A")
            col = 3 + _display_width(buffer[:cursor_pos])
            sys.stdout.write(f"[{col}G")

            sys.stdout.flush()

        redraw()
        while True:
            ch = msvcrt.getwch()

            if ch == "\r":  # Enter
                if suggestions and selected < len(suggestions):
                    buffer = suggestions[selected]["cmd"]
                # 清除输入行 + 下拉框（\033[J 清除光标到屏幕底部）
                sys.stdout.write("\033[0G\033[J")
                sys.stdout.flush()
                if buffer and (not _input_history or _input_history[-1] != buffer):
                    _input_history.append(buffer)
                print()
                return buffer.strip()

            elif ch == "\xe0":  # 功能键前缀
                ch2 = msvcrt.getwch()
                if suggestions and ch2 in ("H", "P"):
                    if ch2 == "H":  # ↑
                        selected = max(0, selected - 1)
                    else:  # ↓
                        selected = min(len(suggestions) - 1, selected + 1)
                    redraw()
                elif not suggestions and ch2 == "H" and _input_history:
                    if history_pos == -1:
                        history_pos = len(_input_history) - 1
                    elif history_pos > 0:
                        history_pos -= 1
                    else:
                        continue
                    buffer = _input_history[history_pos]
                    cursor_pos = len(buffer)
                    suggestions = _get_suggestions(buffer, all_cmds) if buffer.startswith("/") else []
                    selected = 0
                    redraw()
                elif not suggestions and ch2 == "P" and history_pos != -1:
                    history_pos += 1
                    if history_pos >= len(_input_history):
                        history_pos = -1
                        buffer = ""
                    else:
                        buffer = _input_history[history_pos]
                    cursor_pos = len(buffer)
                    suggestions = _get_suggestions(buffer, all_cmds) if buffer.startswith("/") else []
                    selected = 0
                    redraw()

                elif ch2 == "K":  # ← 左方向键
                    if cursor_pos > 0:
                        cursor_pos -= 1
                    redraw()

                elif ch2 == "M":  # → 右方向键
                    if cursor_pos < len(buffer):
                        cursor_pos += 1
                    redraw()

            elif ch == "\t":  # Tab → 补全
                if suggestions and selected < len(suggestions):
                    buffer = suggestions[selected]["cmd"]
                    cursor_pos = len(buffer)
                    suggestions = _get_suggestions(buffer, all_cmds) if buffer.startswith("/") else []
                    selected = 0
                    redraw()

            elif ch in ("\x7f", "\x08"):  # Backspace
                if cursor_pos > 0:
                    buffer = buffer[:cursor_pos - 1] + buffer[cursor_pos:]
                    cursor_pos -= 1
                suggestions = _get_suggestions(buffer, all_cmds) if buffer.startswith("/") else []
                selected = 0
                history_pos = -1
                redraw()

            elif ch == "\x1b":  # Esc → 关闭下拉
                if suggestions:
                    suggestions = []
                    selected = 0
                    redraw()

            elif ch == "\x03":  # Ctrl+C
                raise KeyboardInterrupt

            elif ch == "\x15":  # Ctrl+U → 清空行
                buffer = ""
                cursor_pos = 0
                suggestions = []
                selected = 0
                history_pos = -1
                redraw()

            else:
                # getwch 返回的是 Unicode 字符串，直接判断可打印即可
                if ch.isprintable():
                    buffer = buffer[:cursor_pos] + ch + buffer[cursor_pos:]
                    cursor_pos += 1
                    suggestions = _get_suggestions(buffer, all_cmds) if buffer.startswith("/") else []
                    selected = 0
                    history_pos = -1
                    redraw()

    # ── 读取配置 ──
    # 读取配置
    try:
        config = get_config()
    except RuntimeError as e:
        print(f"[错误] {e}")
        sys.exit(1)

    # 欢迎信息
    print(DOG)
    model_name = config["model"]
    print(f"  {Style.colored('Mini Claude v0.1', Style.CYAN, bold=True)}  "
          f"{Style.muted('|')}  {Style.colored(model_name, Style.YELLOW)}")

    # ── MCP 初始化 ──
    mcp_tools = []
    mcp_manager = MCPManager()
    try:
        mcp_tools = mcp_manager.start_all()
    except Exception as e:
        print(f"  {Style.colored(f'[MCP 初始化异常] {e}', Style.RED)}")

    combined_tools = TOOLS + mcp_tools
    tool_count = len(TOOLS)
    mcp_count = len(mcp_tools)
    suffix = f" · MCP {mcp_count} 个服务器" if mcp_count else ""
    print(f"  {Style.muted(f'工具 {tool_count} 个 · 支持 skill · 知识库{suffix}')}")
    print(f"  {Style.command('/exit ')} {Style.muted('退出')}  "
          f"{Style.command('/clear')} {Style.muted('清空')}  "
          f"{Style.command('/context')} {Style.muted('上下文')}  "
          f"{Style.command('/compact')} {Style.muted('压缩')}")
    print(f"  {Style.command('/tools')} {Style.muted('工具')}  "
          f"{Style.command('/skills')} {Style.muted('skill')}  "
          f"{Style.command('/kb')} {Style.muted('知识库')}")

    ctx_window = _get_context_window(config["model"])
    messages = [{"role": "system", "content": _build_system_prompt(ctx_window)}]
    all_cmds = _build_command_list()

    # 工具执行路由：内置工具走 execute_tool，MCP 工具走 mcp_manager
    def _tool_executor(name: str, args: dict) -> str:
        parsed = MCPManager.parse_tool_name(name)
        if parsed and mcp_manager.is_connected(parsed[0]):
            server_name, tool_name = parsed
            return mcp_manager.call_tool(server_name, tool_name, args)
        return execute_tool(name, args)

    def run_turn(user_content: str) -> None:
        """一轮对话：追加 user 消息 → 必要时自动压缩 → 跑 agent → 追加结果。

        user_content 已是最终要发给模型的内容（普通对话是原文，skill 是
        构造好的指令+参数）。自动压缩会保留最后这条 user 消息原文。
        """
        messages.append({"role": "user", "content": user_content})

        # 超过上下文窗口 90% → 自动压缩（保留当前 user 消息）
        _maybe_auto_compact(messages, config, ctx_window)

        try:
            stop = threading.Event()
            spinner = threading.Thread(target=thinking_indicator, args=(stop,), daemon=True)
            spinner.start()
            try:
                result = run_agent(messages, combined_tools, tool_executor=_tool_executor)
            finally:
                stop.set()
                spinner.join(timeout=0.5)
            print(f"\n{result}")
            messages.append({"role": "assistant", "content": result})
        except Exception as e:
            print(f"\n  {Style.colored(f'[错误] {e}', Style.RED)}")
            messages.pop()

    while True:
        try:
            user_input = _input_with_completion(all_cmds)
        except (EOFError, KeyboardInterrupt):
            print(f"  {Style.colored('再见！', Style.GREEN)}")
            mcp_manager.shutdown_all()
            break

        if not user_input:
            continue

        # 特殊命令
        if user_input == "/exit":
            print(f"\n  {Style.colored('再见！', Style.GREEN)}")
            mcp_manager.shutdown_all()
            break

        if user_input == "/clear":
            ctx_window = _get_context_window(config["model"])
            messages = [{"role": "system", "content": _build_system_prompt(ctx_window)}]
            clear_read_cache()
            print(f"  {Style.colored('对话历史已清空', Style.GREEN)}")
            continue

        if user_input == "/tools":
            print(f"\n  {Style.command('可用工具')} "
                  f"{Style.muted(f'({len(TOOLS)} 个)')}")
            for t in TOOLS:
                name = t["function"]["name"]
                desc = t["function"]["description"]
                print(f"  {Style.highlight(f'  {name}')}: {desc}")
            continue

        if user_input == "/skills":
            skills = list_skills()
            if not skills:
                print(f"  {Style.colored('没有可用的 skill', Style.YELLOW)}。"
                      f"在 {Style.muted('~/.mini_claude/skills/<name>/SKILL.md')} 添加")
                continue

            name = _interactive_skill_picker(skills)
            if name is None:
                continue

            # 构造 skill 指令 user 消息，复用正常对话路径执行
            prompt = build_skill_prompt(name, "")
            if prompt is None:
                print(f"  {Style.colored(f'[错误] skill 不存在: {name}', Style.RED)}")
                continue
            print(f"  {Style.command(f'执行 skill: {name}')}")
            run_turn(prompt)
            continue

        # /kb 命令
        if user_input.startswith("/kb"):
            parts = user_input.strip().split()
            if len(parts) == 1:
                print("用法: /kb rebuild  重建索引\n     /kb status   查看索引状态")
            elif parts[1] == "rebuild":
                print("正在重建知识库索引...")
                try:
                    msg = build_index(config, _KB_DIR, _KB_INDEX_DIR)
                    print(msg)
                except Exception as e:
                    print(f"[错误] 重建索引失败: {e}")
            elif parts[1] == "status":
                info = get_index_info(_KB_INDEX_DIR)
                if info["exists"]:
                    print(f"索引状态: 已构建")
                    print(f"  文本块: {info['total_chunks']}")
                    print(f"  文件:   {', '.join(info['files'])}")
                else:
                    print("索引状态: 未构建（使用 /kb rebuild 构建）")
            else:
                print(f"未知命令: /kb {parts[1]}")
            continue

        # ── /context ──
        if user_input == "/context":
            tokens, breakdown = _estimate_tokens(messages)
            ctx_window = _get_context_window(config["model"])
            pct = tokens / ctx_window * 100
            msg_count = len(messages)

            print(f"\n{Style.command('上下文使用情况')}:")
            print(f"  {Style.highlight(f'{tokens:,}')} / {ctx_window:,} tokens  "
                  f"({Style.colored(f'{pct:.1f}%', Style.YELLOW if pct > 70 else Style.GREEN)})")
            print(f"  消息数: {msg_count}")
            if msg_count > 1:
                roles = {}
                for m in messages:
                    r = m["role"]
                    roles[r] = roles.get(r, 0) + 1
                role_detail = "  ".join(f"{Style.muted(r)}: {c}" for r, c in sorted(roles.items()))
                print(f"  角色分布: {role_detail}")
            print(f"  模型: {config['model']}")
            continue

        # ── /compact ──
        if user_input == "/compact":
            if len(messages) <= 1:
                print(f"  {Style.colored('对话很短，无需压缩', Style.YELLOW)}")
                continue

            old_tokens, _ = _estimate_tokens(messages)
            old_count = len(messages)

            print(f"  {Style.command('压缩中')} {Style.muted('对话历史...')}")
            stop = threading.Event()
            spinner = threading.Thread(target=thinking_indicator, args=(stop,), daemon=True)
            spinner.start()

            try:
                new_messages, summary = _compact_messages(messages, config)
                messages.clear()
                messages.extend(new_messages)
                new_tokens, _ = _estimate_tokens(messages)

                # 摘要预览（前 2 行）
                preview_lines = summary.strip().split("\n")[:2]
                preview = " ".join(line.strip() for line in preview_lines)

                print(f"\n{Style.command('[压缩完成]')}")
                print(f"  {Style.muted('消息数:')} {old_count} → {len(messages)}")
                print(f"  {Style.muted('token:')} {old_tokens:,} → {new_tokens:,} "
                      f"({Style.highlight(f'节省 {old_tokens - new_tokens:,}')})")
                print(f"  {Style.muted('摘要预览:')} {preview[:120]}...")
            except Exception as e:
                print(f"\n  {Style.colored(f'[错误] 压缩失败: {e}', Style.RED)}")
            finally:
                stop.set()
                spinner.join(timeout=0.5)
            continue

        # /xxx → 尝试当作 skill 执行
        if user_input.startswith("/"):
            skill_parts = user_input[1:].split()
            if not skill_parts:
                # 只有 "/" 没有内容，忽略
                continue
            skill_name = skill_parts[0]
            skill_args = user_input[len(skill_name) + 2:]
            prompt = build_skill_prompt(skill_name, skill_args)
            if prompt is not None:
                print(f"  {Style.command(f'执行 skill: {skill_name}')}")
                run_turn(prompt)
                continue
            print(f"  {Style.colored(f'[错误] 未知命令: {user_input}', Style.RED)}")
            continue

        # 正常对话
        run_turn(user_input)


if __name__ == "__main__":
    main()
