"""msvcrt 和 ANSI 清屏诊断"""

import msvcrt, sys, os

print("=== msvcrt + ANSI 诊断 ===")
print("请在上面输入任意字符，然后按 Enter 确认。")
print("按 Esc 退出。")
print()

buf = ""
sys.stdout.write("> ")
sys.stdout.flush()

while True:
    ch = msvcrt.getch()

    if ch == b"\r":  # Enter
        print()
        break
    elif ch == b"\x1b":  # Esc
        print("\n[退出]")
        sys.exit(0)
    elif ch == b"\xe0":  # 方向键等，跳过
        msvcrt.getch()
        continue
    elif ch in (b"\x7f", b"\x08"):  # Backspace
        buf = buf[:-1]
        sys.stdout.write("\r\033[K> " + buf)
        sys.stdout.flush()
    elif b" " <= ch <= b"~":  # 可打印字符
        buf += ch.decode()
        sys.stdout.write("\r\033[K> " + buf)
        sys.stdout.flush()

print("你输入了:", repr(buf))
print()

# 测试 \033[J
print("按任意键测试 \\033[J 清屏效果...")
msvcrt.getch()
sys.stdout.write("\r\033[J")
sys.stdout.flush()
print("清屏后这里应该只有这行字")
print()

print("诊断完成。")
