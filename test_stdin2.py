"""诊断：逐步测试 msvcrt + 终端输出"""

import msvcrt, sys, os

# 测试1：纯 print 是否能看到
print("=== 测试1: print() 能看到吗 ===")
print("如果你能看到这行字，print() 正常。")

# 测试2：sys.stdout.write + flush
print("\n=== 测试2: sys.stdout.write + flush ===")
sys.stdout.write("write测试: OK\n")
sys.stdout.flush()

# 测试3：msvcrt 是否能读到键
print("\n=== 测试3: msvcrt.getch() 读取 ===")
print("按下字母 a (不要按回车)")
ch = msvcrt.getch()
print(f"\n读到: {ch}  (显示为 {ch!r})")

# 测试4：用 input() 是否正常
print("\n=== 测试4: input() 标准输入 ===")
s = input("input> ")
print(f"你输入了: {s}")

# 测试5：\r + write 是否工作（无ANSI）
print("\n=== 测试5: \\r + write (无 ANSI) ===")
sys.stdout.write("-> 输入字符然后按 Enter\n")
buf = ""
sys.stdout.write("> ")
sys.stdout.flush()
while True:
    ch = msvcrt.getch()
    if ch == b"\r":
        print()
        break
    elif ch == b"\xe0":
        msvcrt.getch()
        continue
    elif ch in (b"\x7f", b"\x08"):
        buf = buf[:-1]
        sys.stdout.write("\033[0G\033[K> " + buf)
        sys.stdout.flush()
    elif b" " <= ch <= b"~":
        buf += ch.decode()
        sys.stdout.write("\033[0G\033[K> " + buf)
        sys.stdout.flush()
print(f"你输入了: {buf}")

# 测试6：\033序列是否能处理
print("\n=== 测试6: ANSI \\033 序列 ===")
sys.stdout.write("\033[92m绿色文字\033[0m 应该可见\n")
sys.stdout.flush()

print("\n诊断完成")
