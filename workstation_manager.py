#!/usr/bin/env python
"""工作站 SSH 连接管理器 — 自动配置、保活、重连。

用法:
    python workstation_manager.py setup      # 交互式输入 IP/用户名/密码,生成 .env + 密钥
    python workstation_manager.py check      # 检查连接状态
    python workstation_manager.py keepalive  # 持续保活(每60秒检测+重连)
    python workstation_manager.py doctor     # 调用 fluid-worker doctor 验证工作站环境

支持通过命令行参数非交互式配置:
    python workstation_manager.py setup --host 10.129.177.241 --user ls --password xxx
    python workstation_manager.py setup --host 10.129.177.241 --user ls --key C:/path/to/key
"""

from __future__ import annotations

import argparse
import getpass
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

# 项目根目录(脚本放在项目根或 scripts/ 下都能自动定位)
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR
if PROJECT_ROOT.name == "scripts":
    PROJECT_ROOT = PROJECT_ROOT.parent

ENV_FILE = PROJECT_ROOT / ".env"
SSH_DIR = Path.home() / ".ssh"
DEFAULT_KEY = SSH_DIR / "fluid_scientist_ed25519"
DEFAULT_KNOWN_HOSTS = SSH_DIR / "fluid_scientist_known_hosts"

# 颜色输出
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"


def cprint(msg: str, color: str = "") -> None:
    print(f"{color}{msg}{RESET}")


def run_cmd(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """运行命令,返回 (exit_code, stdout, stderr)。"""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except FileNotFoundError:
        return -2, "", f"command not found: {cmd[0]}"
    except Exception as e:
        return -3, "", str(e)


def check_ssh_key(key_path: Path) -> bool:
    """检查 SSH 私钥是否存在且格式正确。"""
    if not key_path.exists():
        return False
    content = key_path.read_text(encoding="utf-8", errors="ignore")
    return "BEGIN OPENSSH PRIVATE KEY" in content or "BEGIN RSA PRIVATE KEY" in content


def generate_ssh_keypair(key_path: Path) -> bool:
    """生成 ed25519 密钥对。"""
    key_path.parent.mkdir(parents=True, exist_ok=True)
    code, _, err = run_cmd([
        "ssh-keygen", "-t", "ed25519",
        "-f", str(key_path),
        "-N", "",  # 无密码
        "-C", "fluid_scientist_workstation",
    ])
    if code != 0:
        cprint(f"  密钥生成失败: {err}", RED)
        return False
    cprint(f"  密钥已生成: {key_path}", GREEN)
    return True


def install_public_key_to_host(
    host: str, user: str, password: str, pub_key_path: Path
) -> bool:
    """通过密码将公钥安装到远程主机的 authorized_keys。"""
    if not pub_key_path.exists():
        cprint(f"  公钥文件不存在: {pub_key_path}", RED)
        return False
    pub_key = pub_key_path.read_text(encoding="utf-8").strip()

    # 使用 sshpass + ssh 安装公钥
    # 如果没有 sshpass,尝试用 ssh-copy-id
    code, _, err = run_cmd(["where", "sshpass"])
    has_sshpass = code == 0

    if has_sshpass:
        cmd = [
            "sshpass", "-p", password,
            "ssh",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=10",
            f"{user}@{host}",
            f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
            f"echo '{pub_key}' >> ~/.ssh/authorized_keys && "
            f"chmod 600 ~/.ssh/authorized_keys && "
            f"echo 'key installed'",
        ]
        code, out, err = run_cmd(cmd, timeout=15)
        if code == 0 and "key installed" in out:
            cprint(f"  公钥已安装到 {host}", GREEN)
            return True
        cprint(f"  sshpass 安装公钥失败: {err}", RED)
        return False
    else:
        # 尝试 ssh-copy-id (需要交互式输入密码)
        cprint("  未找到 sshpass,尝试使用 ssh-copy-id...", YELLOW)
        cprint("  请手动输入密码 (如果提示):", YELLOW)
        code = subprocess.call([
            "ssh-copy-id",
            "-i", str(pub_key_path),
            "-o", "StrictHostKeyChecking=accept-new",
            f"{user}@{host}",
        ])
        if code == 0:
            cprint(f"  公钥已安装到 {host}", GREEN)
            return True
        cprint(f"  ssh-copy-id 失败 (exit={code})", RED)
        return False


def add_to_known_hosts(host: str, known_hosts_path: Path) -> bool:
    """将主机添加到 known_hosts 文件。"""
    known_hosts_path.parent.mkdir(parents=True, exist_ok=True)
    code, out, err = run_cmd([
        "ssh-keyscan", "-H", host,
    ], timeout=15)
    if code != 0 or not out.strip():
        cprint(f"  ssh-keyscan 失败: {err}", RED)
        return False

    # 追加到 known_hosts 文件
    with open(known_hosts_path, "a", encoding="utf-8") as f:
        f.write(out)
    cprint(f"  主机 {host} 已添加到 {known_hosts_path}", GREEN)
    return True


def test_ssh_connection(
    host: str, user: str, port: int, key_path: Path, known_hosts: Path
) -> bool:
    """测试 SSH 连接是否正常。"""
    cmd = [
        "ssh",
        "-p", str(port),
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={known_hosts}",
        "-o", "ConnectTimeout=10",
    ]
    if key_path and key_path.exists():
        cmd.extend(["-i", str(key_path)])
    cmd.append(f"{user}@{host}")
    cmd.extend(["echo", "CONNECTION_OK"])

    code, out, err = run_cmd(cmd, timeout=15)
    return bool(code == 0 and "CONNECTION_OK" in out)


def write_env_file(
    host: str, user: str, port: int, key_path: Path, known_hosts: Path
) -> None:
    """生成或更新 .env 文件。"""
    env_content = f"""# Fluid Scientist 工作站配置 — 自动生成
# 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}

# OpenFOAM 工作站
FLUID_WORKSTATION__HOSTS=["{host}"]
FLUID_WORKSTATION__USERNAME={user}
FLUID_WORKSTATION__PORT={port}
FLUID_WORKSTATION__IDENTITY_FILE={key_path.as_posix()}
FLUID_WORKSTATION__KNOWN_HOSTS_FILE={known_hosts.as_posix()}

# Real integration 模式
FLUID_APP_MODE=fake
"""

    # 如果已有 .env,读取旧内容并保留非工作站相关的行
    if ENV_FILE.exists():
        old_lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
        preserved = []
        for line in old_lines:
            if line.startswith("#") or not line.strip():
                continue
            if any(
                line.startswith(prefix)
                for prefix in [
                    "FLUID_WORKSTATION__",
                    "FLUID_APP_MODE",
                ]
            ):
                continue
            preserved.append(line)

        if preserved:
            env_content += "\n# 保留的旧配置\n" + "\n".join(preserved) + "\n"

    ENV_FILE.write_text(env_content, encoding="utf-8")
    cprint(f"  .env 已生成: {ENV_FILE}", GREEN)


def cmd_setup(args: argparse.Namespace) -> int:
    """交互式或命令行参数配置工作站连接。"""
    cprint("\n=== 工作站 SSH 配置 ===", CYAN)

    host = args.host or input("工作站 IP 地址: ").strip()
    if not host:
        cprint("IP 地址不能为空", RED)
        return 1

    user = args.user or input("SSH 用户名: ").strip()
    if not user:
        cprint("用户名不能为空", RED)
        return 1

    port = args.port or int(input("SSH 端口 [22]: ").strip() or "22")

    key_path = Path(args.key) if args.key else DEFAULT_KEY
    known_hosts = DEFAULT_KNOWN_HOSTS

    cprint("\n配置信息:", CYAN)
    cprint(f"  主机: {host}:{port}")
    cprint(f"  用户: {user}")
    cprint(f"  密钥: {key_path}")
    cprint(f"  known_hosts: {known_hosts}")

    # Step 1: 检查/生成密钥
    cprint("\n[1/4] 检查 SSH 密钥...", CYAN)
    if check_ssh_key(key_path):
        cprint(f"  密钥已存在: {key_path}", GREEN)
    else:
        cprint("  密钥不存在,生成新密钥...", YELLOW)
        if not generate_ssh_keypair(key_path):
            return 1

    # Step 2: 安装公钥到远程主机
    cprint("\n[2/4] 安装公钥到工作站...", CYAN)
    pub_key = key_path.with_suffix(".pub")

    # 先测试是否已经可以免密连接
    if test_ssh_connection(host, user, port, key_path, known_hosts):
        cprint("  已可免密连接,跳过公钥安装", GREEN)
    else:
        # 需要 known_hosts 先接受主机
        if not known_hosts.exists() or host not in known_hosts.read_text(
            encoding="utf-8", errors="ignore"
        ):
            add_to_known_hosts(host, known_hosts)

        password = args.password or getpass.getpass(
            f"{user}@{host} 的密码 (用于安装公钥,仅此一次): "
        )
        if not install_public_key_to_host(host, user, password, pub_key):
            cprint("  公钥安装失败,请手动执行 ssh-copy-id", RED)
            return 1

    # Step 3: 确保 known_hosts 包含目标主机
    cprint("\n[3/4] 更新 known_hosts...", CYAN)
    if not known_hosts.exists() or host not in known_hosts.read_text(
        encoding="utf-8", errors="ignore"
    ):
        add_to_known_hosts(host, known_hosts)
    else:
        cprint(f"  {host} 已在 known_hosts 中", GREEN)

    # Step 4: 测试连接
    cprint("\n[4/4] 测试 SSH 连接...", CYAN)
    if test_ssh_connection(host, user, port, key_path, known_hosts):
        cprint("  连接成功!", GREEN)
    else:
        cprint("  连接失败!请检查网络/密钥/known_hosts", RED)
        return 1

    # 生成 .env 文件
    write_env_file(host, user, port, key_path, known_hosts)

    cprint("\n=== 配置完成 ===", GREEN)
    cprint(f"  .env 文件: {ENV_FILE}")
    cprint("  现在可以启动服务: uvicorn fluid_scientist.api.app:create_app --factory --port 8000")
    return 0


def cmd_check() -> int:
    """检查当前工作站连接状态。"""
    cprint("\n=== 工作站连接检查 ===", CYAN)

    if not ENV_FILE.exists():
        cprint(f"  .env 文件不存在: {ENV_FILE}", RED)
        cprint("  请先运行: python workstation_manager.py setup", YELLOW)
        return 1

    # 读取 .env 中的配置
    env_vars = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env_vars[key.strip()] = value.strip()

    host = env_vars.get("FLUID_WORKSTATION__HOSTS", "[]").strip("[]\"")
    user = env_vars.get("FLUID_WORKSTATION__USERNAME", "")
    port = int(env_vars.get("FLUID_WORKSTATION__PORT", "22"))
    key_str = env_vars.get("FLUID_WORKSTATION__IDENTITY_FILE", "")
    kh_str = env_vars.get("FLUID_WORKSTATION__KNOWN_HOSTS_FILE", "")

    if not host or not user:
        cprint("  .env 中缺少工作站配置", RED)
        return 1

    cprint(f"  主机: {host}:{port}")
    cprint(f"  用户: {user}")
    cprint(f"  密钥: {key_str}")
    cprint(f"  known_hosts: {kh_str}")

    # 检查 TCP 连通性
    cprint("\n[TCP] 检查端口连通性...", CYAN)
    try:
        sock = socket.create_connection((host, port), timeout=5)
        sock.close()
        cprint(f"  TCP {host}:{port} 可达", GREEN)
    except Exception as e:
        cprint(f"  TCP {host}:{port} 不可达: {e}", RED)
        return 1

    # 检查密钥文件
    cprint("\n[KEY] 检查密钥文件...", CYAN)
    key_path = Path(key_str)
    if check_ssh_key(key_path):
        cprint(f"  密钥有效: {key_path}", GREEN)
    else:
        cprint(f"  密钥无效或不存在: {key_path}", RED)
        return 1

    # 检查 known_hosts
    cprint("\n[HOSTS] 检查 known_hosts...", CYAN)
    kh_path = Path(kh_str)
    if kh_path.exists() and host in kh_path.read_text(encoding="utf-8", errors="ignore"):
        cprint(f"  {host} 在 known_hosts 中", GREEN)
    else:
        cprint(f"  {host} 不在 known_hosts 中", RED)
        return 1

    # 测试 SSH 连接
    cprint("\n[SSH] 测试 SSH 连接...", CYAN)
    if test_ssh_connection(host, user, port, key_path, kh_path):
        cprint("  SSH 连接成功!", GREEN)
    else:
        cprint("  SSH 连接失败!", RED)
        return 1

    # 测试 fluid-worker doctor
    cprint("\n[DOCTOR] 调用 fluid-worker doctor...", CYAN)
    cmd = [
        "ssh",
        "-p", str(port),
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={kh_str}",
        "-i", str(key_path),
        f"{user}@{host}",
        "fluid-worker", "doctor", "--json",
    ]
    code, out, err = run_cmd(cmd, timeout=30)
    if code == 0 and out.strip():
        try:
            doc = json.loads(out.strip().splitlines()[-1])
            cprint(f"  OpenFOAM: {doc.get('foam_version', 'unknown')}", GREEN)
            cprint(f"  CPU: {doc.get('cpu_count', 'unknown')} cores", GREEN)
            cprint(f"  Worker: {doc.get('worker_version', 'unknown')}", GREEN)
        except (json.JSONDecodeError, IndexError):
            cprint(f"  doctor 返回非 JSON: {out[:200]}", YELLOW)
    else:
        cprint(f"  doctor 失败: {err or out[:200]}", YELLOW)
        cprint("  (工作站可能未安装 fluid-worker,但不影响 SSH 连接)", YELLOW)

    cprint("\n=== 检查完成 ===", GREEN)
    return 0


def cmd_keepalive(interval: int = 60) -> int:
    """持续保活,定期检测并重连。"""
    cprint(f"\n=== 工作站保活模式 (间隔 {interval}s) ===", CYAN)
    cprint("  按 Ctrl+C 停止\n")

    if not ENV_FILE.exists():
        cprint("  .env 文件不存在,请先 setup", RED)
        return 1

    # 读取配置
    env_vars = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env_vars[key.strip()] = value.strip()

    host = env_vars.get("FLUID_WORKSTATION__HOSTS", "[]").strip("[]\"")
    user = env_vars.get("FLUID_WORKSTATION__USERNAME", "")
    port = int(env_vars.get("FLUID_WORKSTATION__PORT", "22"))
    key_str = env_vars.get("FLUID_WORKSTATION__IDENTITY_FILE", "")
    kh_str = env_vars.get("FLUID_WORKSTATION__KNOWN_HOSTS_FILE", "")

    consecutive_failures = 0
    check_count = 0

    while True:
        check_count += 1
        timestamp = time.strftime("%H:%M:%S")

        # TCP 检测
        try:
            sock = socket.create_connection((host, port), timeout=5)
            sock.close()
            tcp_ok = True
        except Exception:
            tcp_ok = False

        if tcp_ok:
            # SSH 检测
            ssh_ok = test_ssh_connection(host, user, port, Path(key_str), Path(kh_str))
        else:
            ssh_ok = False

        if ssh_ok:
            consecutive_failures = 0
            cprint(
                f"  [{timestamp}] #{check_count} 连接正常",
                GREEN,
            )
        else:
            consecutive_failures += 1
            cprint(
                f"  [{timestamp}] #{check_count} 连接失败 "
                f"(连续 {consecutive_failures} 次)",
                RED,
            )

            # 尝试修复 known_hosts (主机 key 可能变了)
            if tcp_ok and consecutive_failures <= 2:
                cprint("  尝试更新 known_hosts...", YELLOW)
                kh_path = Path(kh_str)
                # 移除旧条目并重新扫描
                if kh_path.exists():
                    run_cmd([
                        "ssh-keygen", "-R", host,
                    ], timeout=10)
                add_to_known_hosts(host, kh_path)

                # 重试
                if test_ssh_connection(host, user, port, Path(key_str), kh_path):
                    cprint("  known_hosts 更新后连接恢复!", GREEN)
                    consecutive_failures = 0
                else:
                    cprint("  仍然无法连接", RED)

        time.sleep(interval)


def cmd_doctor() -> int:
    """调用远程 fluid-worker doctor。"""
    cprint("\n=== 工作站环境检查 ===", CYAN)

    if not ENV_FILE.exists():
        cprint("  .env 不存在,请先 setup", RED)
        return 1

    env_vars = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env_vars[key.strip()] = value.strip()

    host = env_vars.get("FLUID_WORKSTATION__HOSTS", "[]").strip("[]\"")
    user = env_vars.get("FLUID_WORKSTATION__USERNAME", "")
    port = int(env_vars.get("FLUID_WORKSTATION__PORT", "22"))
    key_str = env_vars.get("FLUID_WORKSTATION__IDENTITY_FILE", "")
    kh_str = env_vars.get("FLUID_WORKSTATION__KNOWN_HOSTS_FILE", "")

    cmd = [
        "ssh",
        "-p", str(port),
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={kh_str}",
        "-i", str(key_str),
        f"{user}@{host}",
        "fluid-worker", "doctor", "--json",
    ]
    code, out, err = run_cmd(cmd, timeout=30)
    if code == 0:
        print(out)
        return 0
    else:
        cprint(f"doctor 失败: {err or out}", RED)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fluid Scientist 工作站 SSH 连接管理器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s setup --host 10.129.177.241 --user ls
  %(prog)s setup --host 10.129.177.241 --user ls --password mypass
  %(prog)s check
  %(prog)s keepalive --interval 60
  %(prog)s doctor
""",
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # setup
    setup_parser = subparsers.add_parser("setup", help="配置工作站连接")
    setup_parser.add_argument("--host", help="工作站 IP 地址")
    setup_parser.add_argument("--user", help="SSH 用户名")
    setup_parser.add_argument("--port", type=int, default=22, help="SSH 端口 (默认 22)")
    setup_parser.add_argument("--password", help="SSH 密码 (用于安装公钥,仅此一次)")
    setup_parser.add_argument("--key", help="SSH 私钥路径 (默认 ~/.ssh/fluid_scientist_ed25519)")

    # check
    subparsers.add_parser("check", help="检查连接状态")

    # keepalive
    ka_parser = subparsers.add_parser("keepalive", help="持续保活")
    ka_parser.add_argument("--interval", type=int, default=60, help="检测间隔秒数 (默认 60)")

    # doctor
    subparsers.add_parser("doctor", help="调用远程 fluid-worker doctor")

    args = parser.parse_args()

    if args.command == "setup":
        return cmd_setup(args)
    elif args.command == "check":
        return cmd_check()
    elif args.command == "keepalive":
        return cmd_keepalive(args.interval)
    elif args.command == "doctor":
        return cmd_doctor()
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
