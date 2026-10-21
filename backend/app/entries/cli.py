"""CLI 入口：终端 REPL。与 Web 入口并列，消费同一套 core / infra / 持久层。

只负责终端交互与渲染：命令补全、ANSI 配色、thinking 动画、诊断输出。
业务逻辑全部来自 app.core / app.infra / app.tools。
"""

from __future__ import annotations

import os
import sys
import threading
import time

from app.core import triage_rules
from app.core.context import compact_messages, estimate_tokens, get_context_window
from app.core.events import COMPACT, TEXT
from app.db.base import session_scope
from app.db.repositories import sessions as sessions_repo
from app.infra import health
from app.infra.config import ConfigError, get_settings, require_api_key
from app.infra.knowledge import build_index, get_index_info
from app.infra.mcp import MCPManager
from app.infra.skills import build_skill_prompt, list_skills
from app.services import chat_service
from app.services.session_service import SessionRuntime
from app.services.stream_broker import CLOSED, EventStream
from app.tools import TOOLS, clear_read_cache


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


# ── 欢迎画面 ──

LOGO = r"""
     +----------------+
     |       ++       |
     |     ++++++     |
     |       ++       |
     +----------------+
"""

DISCLAIMER = "本工具仅提供就诊科室参考，不构成医疗诊断；急危重症请立即拨打 120。"


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


def _print_auto_diagnosis(e: Exception | str) -> None:
    """异常自动诊断：归类疑似模块 + 体检有问题的项，附下一步建议。

    只列有问题/警告的项，不刷屏；诊断本身失败时静默跳过，不影响主流程。
    参数可以是异常对象，也可以是异常类名（事件流里的 error 事件只带类名）。
    """
    try:
        suspected = health.classify_exception(e)
        results = health.check_all()
        bad = health.problems(results)
    except Exception:
        return

    if not suspected and not bad:
        return
    print(f"\n  {Style.colored('诊断:', Style.YELLOW)}")
    for item in suspected:
        print(f"    {Style.highlight('疑似模块:')} {item['module']} — {item['reason']}")
        print(f"      {Style.muted('建议:')} {item['suggestion']}")
    for r in bad:
        mark = {"ok": "OK", "warn": "WARN", "error": "ERR", "skip": "SKIP"}[r["status"]]
        color = {"ok": Style.GREEN, "warn": Style.YELLOW,
                 "error": Style.RED, "skip": Style.GRAY}[r["status"]]
        print(f"    {Style.colored(mark, color)} {r['name']}: {Style.muted(r['detail'])}")


def _print_compact_notice(event: dict) -> None:
    """渲染自动压缩提示（压缩本身发生在 chat_service 内）。"""
    if event.get("degraded"):
        reason = event.get("error", "")
        print(f"{Style.colored(f'  [自动压缩失败: {reason}，已丢弃早期历史]', Style.YELLOW)}")
        return
    before = event.get("before_tokens", 0)
    after = event.get("after_tokens", 0)
    print(f"{Style.muted('  [自动压缩对话历史]')} "
          f"{Style.muted(f'{before:,} → {after:,} tokens')}")


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
            ("/health", "检查各模块状态"),
            ("/ping", "活体探测 API 可用性"),
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
            sys.stdout.write(f"\033[0G\033[K> {buffer}\033[J")

            if suggestions:
                # 画新下拉框
                shown = suggestions[:10]
                new_lines = 1 + 1 + len(shown) + 1  # 空行 + 分隔线 + 项 + 分隔线

                width = min(55, shutil.get_terminal_size().columns - 2)
                sep = f"\033[38;5;244m{'─' * width}\033[0m"

                sys.stdout.write("\033[B\033[0G\033[K")  # 空行
                sys.stdout.write("\033[B\033[0G\033[K" + sep)

                for i, s in enumerate(shown):
                    cmd = s["cmd"]
                    desc = s["desc"]
                    rest = max(0, width - len(cmd) - 4)
                    desc_d = desc[:rest] if rest > 3 else ""
                    line = f"  {'>' if i == selected else ' '} {cmd}  \033[38;5;244m{desc_d}\033[0m"
                    if i == selected:
                        line = f"\033[7m{line}\033[0m"
                    sys.stdout.write("\033[B\033[0G\033[K" + line)

                sys.stdout.write("\033[B\033[0G\033[K" + sep)
                cursor_line = new_lines
            else:
                cursor_line = 0

            # 将光标定位到 buffer 中的正确位置
            if cursor_line > 0:
                sys.stdout.write(f"\033[{cursor_line}A")
            col = 3 + _display_width(buffer[:cursor_pos])
            sys.stdout.write(f"\033[{col}G")

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
    settings = get_settings()
    try:
        require_api_key(settings)
    except ConfigError as e:
        print(f"[错误] {e}")
        sys.exit(1)

    # 欢迎信息
    print(LOGO)
    model_name = settings.model
    print(f"  {Style.colored('智能导诊助手', Style.CYAN, bold=True)}  "
          f"{Style.muted('|')}  {Style.colored(model_name, Style.YELLOW)}")
    print(f"  {Style.muted(DISCLAIMER)}")

    # ── MCP 初始化 ──
    mcp_tools = []
    mcp_manager = MCPManager()
    mcp_lock = threading.Lock()
    health.set_mcp_manager(mcp_manager)
    try:
        mcp_tools = mcp_manager.start_all(settings)
    except Exception as e:
        print(f"  {Style.colored(f'[MCP 初始化异常] {e}', Style.RED)}")
        _print_auto_diagnosis(e)

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
          f"{Style.command('/kb')} {Style.muted('知识库')}  "
          f"{Style.command('/health')} {Style.muted('体检')}  "
          f"{Style.command('/ping')} {Style.muted('探测')}")

    ctx_window = get_context_window(settings.model)
    all_cmds = _build_command_list()

    # ── 会话状态：懒建，真正开聊时才落库，避免留下空会话 ──
    cli_state: dict = {"session_id": None, "runtime": None}

    def ensure_runtime() -> SessionRuntime:
        """取（或建）会话运行时；此时才在库里建会话，source 标记为 cli。"""
        if cli_state["runtime"] is None:
            with session_scope() as db:
                created = sessions_repo.create(db, source="cli", model=settings.model)
                cli_state["session_id"] = created.id
            cli_state["runtime"] = SessionRuntime(session_id=cli_state["session_id"])
        return cli_state["runtime"]

    def run_turn(user_content: str) -> None:
        """一轮对话：走与 Web 完全相同的执行与落库路径（chat_service.run_turn）。

        user_content 已是最终要发给模型的内容（普通对话是原文，skill 是
        构造好的指令+参数）。这里只负责把事件流渲染到终端。
        """
        runtime = ensure_runtime()
        stream = EventStream()
        # 红旗症状由入口层无条件提示，不等待模型判断
        matched = triage_rules.detect(user_content)
        if matched:
            print(f"  {Style.colored(triage_rules.alert_text(matched), Style.RED, bold=True)}")
        # chat_service.run_turn 约定：调用方先拿到会话锁，它负责在结束时释放
        runtime.lock.acquire()
        worker = threading.Thread(
            target=chat_service.run_turn,
            kwargs={
                "runtime": runtime,
                "user_message": user_content,
                "allow_bash": True,
                "stream": stream,
                "settings": settings,
                "tools": combined_tools,
                "mcp": mcp_manager,
                "mcp_lock": mcp_lock,
            },
            daemon=True,
        )

        stop = threading.Event()
        spinner = threading.Thread(target=thinking_indicator, args=(stop,), daemon=True)
        spinner.start()
        worker.start()

        answer = ""
        error_kind = None
        try:
            while True:
                event = stream.get(timeout=0.5)
                if event is None:
                    continue
                if event is CLOSED:
                    break
                etype = event.get("type")
                if etype == TEXT:
                    answer = event.get("content", "")
                elif etype == COMPACT:
                    _print_compact_notice(event)
                elif etype == "error":
                    error_kind = event.get("kind")
                    answer = f"[错误] {event.get('message')}"
        finally:
            stop.set()
            spinner.join(timeout=0.5)
            worker.join(timeout=1)

        print(f"\n{answer}")
        if error_kind:
            _print_auto_diagnosis(error_kind)

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
            # 换一段干净的历史继续聊；旧会话留在库里（Web 侧栏仍可见）
            cli_state["session_id"] = None
            cli_state["runtime"] = None
            clear_read_cache()
            print(f"  {Style.colored('对话历史已清空（下次提问会开一个新会话）', Style.GREEN)}")
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
            prompt = build_skill_prompt(name, "", settings)
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
                    msg = build_index(settings)
                    print(msg)
                except Exception as e:
                    print(f"[错误] 重建索引失败: {e}")
            elif parts[1] == "status":
                info = get_index_info(settings)
                if info["exists"]:
                    print(f"索引状态: 已构建")
                    print(f"  文本块: {info['total_chunks']}")
                    print(f"  文件:   {', '.join(info['files'])}")
                else:
                    print("索引状态: 未构建（使用 /kb rebuild 构建）")
            else:
                print(f"未知命令: /kb {parts[1]}")
            continue

        # ── /health ──
        if user_input == "/health":
            print(f"\n  {Style.command('体检报告')} "
                  f"{Style.muted('(静态检查 · 活体探测用 /ping)')}")
            for r in health.check_all():
                mark = {"ok": "OK  ", "warn": "WARN", "error": "ERR ",
                        "skip": "SKIP"}[r["status"]]
                color = {"ok": Style.GREEN, "warn": Style.YELLOW,
                         "error": Style.RED, "skip": Style.GRAY}[r["status"]]
                print(f"  {Style.colored(mark, color)} {r['name']:<10} "
                      f"{Style.muted(r['detail'])}")
            continue

        # ── /ping ──
        if user_input == "/ping":
            print(f"\n  {Style.command('/ping')} "
                  f"{Style.muted('活体探测（消耗少量 token）')}")
            for r in health.ping(settings):
                mark = "OK " if r["ok"] else "ERR"
                color = Style.GREEN if r["ok"] else Style.RED
                ms = f"{r['ms']}ms"
                print(f"  {Style.colored(mark, color)} {r['name']:<10} "
                      f"{Style.muted(r['detail'])}  {Style.muted(ms)}")
            continue

        # ── /context ──
        if user_input == "/context":
            runtime = cli_state["runtime"]
            history = runtime.messages if runtime else []
            tokens, breakdown = estimate_tokens(history)
            ctx_window = get_context_window(settings.model)
            pct = tokens / ctx_window * 100
            msg_count = len(history)

            print(f"\n{Style.command('上下文使用情况')}:")
            print(f"  {Style.highlight(f'{tokens:,}')} / {ctx_window:,} tokens  "
                  f"({Style.colored(f'{pct:.1f}%', Style.YELLOW if pct > 70 else Style.GREEN)})")
            print(f"  消息数: {msg_count}")
            if msg_count > 1:
                roles = {}
                for m in history:
                    r = m["role"]
                    roles[r] = roles.get(r, 0) + 1
                role_detail = "  ".join(f"{Style.muted(r)}: {c}" for r, c in sorted(roles.items()))
                print(f"  角色分布: {role_detail}")
            print(f"  模型: {settings.model}")
            continue

        # ── /compact ──
        if user_input == "/compact":
            runtime = cli_state["runtime"]
            if runtime is None or len(runtime.messages) <= 1:
                print(f"  {Style.colored('对话很短，无需压缩', Style.YELLOW)}")
                continue

            old_tokens, _ = estimate_tokens(runtime.messages)
            old_count = len(runtime.messages)

            print(f"  {Style.command('压缩中')} {Style.muted('对话历史...')}")
            stop = threading.Event()
            spinner = threading.Thread(target=thinking_indicator, args=(stop,), daemon=True)
            spinner.start()

            try:
                new_messages, summary = compact_messages(runtime.messages, settings)
                runtime.messages[:] = new_messages
                # 手动压缩同样要落库，保持「内存缓冲 == 数据库」
                chat_service.persist(runtime)
                new_tokens, _ = estimate_tokens(runtime.messages)

                # 摘要预览（前 2 行）
                preview_lines = summary.strip().split("\n")[:2]
                preview = " ".join(line.strip() for line in preview_lines)

                print(f"\n{Style.command('[压缩完成]')}")
                print(f"  {Style.muted('消息数:')} {old_count} → {len(runtime.messages)}")
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
            prompt = build_skill_prompt(skill_name, skill_args, settings)
            if prompt is not None:
                print(f"\n  {Style.command('You:')} /{skill_name} {skill_args}".rstrip())
                print(f"  {Style.command(f'执行 skill: {skill_name}')}")
                run_turn(prompt)
                continue
            print(f"  {Style.colored(f'[错误] 未知命令: {user_input}', Style.RED)}")
            continue

        # 正常对话
        print(f"\n  {Style.command('You:')} {user_input}")
        run_turn(user_input)


if __name__ == "__main__":
    main()
