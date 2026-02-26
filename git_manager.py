"""
Git Manager & Deployment Tracker for Gold Chart Project
========================================================
A simple tool to see what files changed, commit changes,
view history, and manage deployments.

Usage:
    python git_manager.py              → Interactive menu
    python git_manager.py status       → Quick status check
    python git_manager.py log          → Recent history
    python git_manager.py commit       → Auto-commit all changes
    python git_manager.py diff         → Show what changed
"""

import subprocess
import sys
import os
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── File categories for smart commit messages ──
FILE_CATEGORIES = {
    "Core Server": ["gold_chart.py", "gold_chart_coinbase.py"],
    "Chart UI":    ["chart.html", "chart_new.html", "chart_coinbase.html"],
    "Dashboard":   ["dashboard.py", "dashboard.html"],
    "Mobile/QR":   ["qr.html"],
    "Deploy":      ["deploy/"],
    "Config":      [".gitignore", "package.json", "playwright.config.js"],
    "Git Tools":   ["git_manager.py"],
    "Legacy":      ["tradingview_automation.py", "plus500_only.py",
                    "fivepaisa_integration.py", "inspect_plus500.py",
                    "keep_alive.py", "realtime_trader.py", "price_check.py"],
}

# Colors for terminal output
class C:
    GREEN  = "\033[92m"
    RED    = "\033[91m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RESET  = "\033[0m"


def run_git(*args, capture=True):
    """Run a git command and return output."""
    cmd = ["git", "-C", PROJECT_DIR] + list(args)
    if capture:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    else:
        return subprocess.run(cmd).returncode


def banner():
    print(f"""
{C.CYAN}╔═══════════════════════════════════════════════════════════╗
║  {C.BOLD}GOLD CHART — Git Manager & Deployment Tracker{C.RESET}{C.CYAN}          ║
║  Track changes · Commit · View history · Deploy           ║
╚═══════════════════════════════════════════════════════════╝{C.RESET}
""")


def categorize_file(filepath):
    """Determine which category a file belongs to."""
    for category, patterns in FILE_CATEGORIES.items():
        for pattern in patterns:
            if filepath.startswith(pattern) or filepath == pattern:
                return category
    return "Other"


# =============================================================================
# COMMANDS
# =============================================================================

def cmd_status():
    """Show modified, added, and deleted files with categories."""
    print(f"\n{C.BOLD}📊 PROJECT STATUS{C.RESET}")
    print(f"{'─'*55}")

    # Check for uncommitted changes
    stdout, _, _ = run_git("status", "--porcelain")

    if not stdout:
        print(f"  {C.GREEN}✓ Working tree clean — no changes{C.RESET}")
        print()
        # Show last commit
        log_out, _, _ = run_git("log", "--oneline", "-1")
        if log_out:
            print(f"  {C.DIM}Last commit: {log_out}{C.RESET}")
        return

    lines = stdout.strip().split("\n")
    modified = []
    added = []
    deleted = []
    untracked = []

    for line in lines:
        status = line[:2].strip()
        filepath = line[3:].strip()
        if status == "M":
            modified.append(filepath)
        elif status == "A":
            added.append(filepath)
        elif status == "D":
            deleted.append(filepath)
        elif status == "??":
            untracked.append(filepath)
        elif status == "MM":
            modified.append(filepath)
        else:
            modified.append(filepath)

    if modified:
        print(f"\n  {C.YELLOW}Modified ({len(modified)}):{C.RESET}")
        for f in modified:
            cat = categorize_file(f)
            print(f"    {C.YELLOW}✎{C.RESET}  {f}  {C.DIM}[{cat}]{C.RESET}")

    if added:
        print(f"\n  {C.GREEN}Added ({len(added)}):{C.RESET}")
        for f in added:
            cat = categorize_file(f)
            print(f"    {C.GREEN}+{C.RESET}  {f}  {C.DIM}[{cat}]{C.RESET}")

    if deleted:
        print(f"\n  {C.RED}Deleted ({len(deleted)}):{C.RESET}")
        for f in deleted:
            print(f"    {C.RED}✕{C.RESET}  {f}")

    if untracked:
        print(f"\n  {C.BLUE}New/Untracked ({len(untracked)}):{C.RESET}")
        for f in untracked:
            cat = categorize_file(f)
            print(f"    {C.BLUE}?{C.RESET}  {f}  {C.DIM}[{cat}]{C.RESET}")

    total = len(modified) + len(added) + len(deleted) + len(untracked)
    print(f"\n  {C.BOLD}Total: {total} file(s) changed{C.RESET}")


def cmd_diff():
    """Show what actually changed in each file."""
    print(f"\n{C.BOLD}📝 CHANGES DIFF{C.RESET}")
    print(f"{'─'*55}")

    # Staged + unstaged diff
    stdout, _, _ = run_git("diff", "--stat")
    if not stdout:
        stdout, _, _ = run_git("diff", "--cached", "--stat")
    if not stdout:
        # Check for untracked files
        status_out, _, _ = run_git("status", "--porcelain")
        if status_out:
            print(f"\n  {C.YELLOW}Untracked/new files — use 'commit' to add them{C.RESET}")
            for line in status_out.split("\n"):
                print(f"    {line}")
        else:
            print(f"  {C.GREEN}✓ No changes to show{C.RESET}")
        return

    print(f"\n{stdout}")

    # Show detailed diff for key files
    print(f"\n{C.BOLD}Detailed changes:{C.RESET}")
    stdout2, _, _ = run_git("diff", "--name-only")
    if stdout2:
        for filepath in stdout2.split("\n"):
            filepath = filepath.strip()
            if not filepath:
                continue
            cat = categorize_file(filepath)
            print(f"\n  {C.CYAN}── {filepath} [{cat}] ──{C.RESET}")
            diff_out, _, _ = run_git("diff", "--no-color", filepath)
            if diff_out:
                # Show first 30 lines of diff
                diff_lines = diff_out.split("\n")
                for line in diff_lines[:30]:
                    if line.startswith("+") and not line.startswith("+++"):
                        print(f"    {C.GREEN}{line}{C.RESET}")
                    elif line.startswith("-") and not line.startswith("---"):
                        print(f"    {C.RED}{line}{C.RESET}")
                    else:
                        print(f"    {line}")
                if len(diff_lines) > 30:
                    print(f"    {C.DIM}... ({len(diff_lines) - 30} more lines){C.RESET}")


def cmd_log(count=15):
    """Show recent commit history."""
    print(f"\n{C.BOLD}📜 COMMIT HISTORY (last {count}){C.RESET}")
    print(f"{'─'*55}")

    stdout, _, _ = run_git("log", f"--oneline", f"-{count}",
                           "--format=%C(yellow)%h%C(reset) %C(green)%ar%C(reset) %s")
    if not stdout:
        print(f"  {C.DIM}No commits yet{C.RESET}")
        return

    for line in stdout.split("\n"):
        print(f"  {line}")


def cmd_log_detailed(count=5):
    """Show detailed commit history with files changed."""
    print(f"\n{C.BOLD}📜 DETAILED HISTORY (last {count}){C.RESET}")
    print(f"{'─'*55}")

    stdout, _, _ = run_git("log", f"-{count}", "--stat",
                           "--format=%n%C(yellow)commit %h%C(reset) — %C(green)%ar%C(reset)%n%C(bold)%s%C(reset)")
    if not stdout:
        print(f"  {C.DIM}No commits yet{C.RESET}")
        return
    print(stdout)


def cmd_commit(message=None):
    """Stage all changes and commit with a smart message."""
    # Stage everything
    run_git("add", "-A")

    # Check if there's anything to commit
    stdout, _, _ = run_git("status", "--porcelain")
    if not stdout:
        print(f"\n  {C.GREEN}✓ Nothing to commit — working tree clean{C.RESET}")
        return

    # Build smart commit message from changed files
    if not message:
        lines = stdout.strip().split("\n")
        categories = set()
        files_changed = []
        for line in lines:
            filepath = line[3:].strip()
            files_changed.append(filepath)
            categories.add(categorize_file(filepath))

        cat_str = ", ".join(sorted(categories))
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        if len(files_changed) == 1:
            message = f"Update {files_changed[0]} [{cat_str}]"
        else:
            message = f"Update {len(files_changed)} files [{cat_str}] — {timestamp}"

    print(f"\n{C.BOLD}💾 COMMITTING CHANGES{C.RESET}")
    print(f"{'─'*55}")
    print(f"  Message: {C.CYAN}{message}{C.RESET}")

    # Show what's being committed
    for line in stdout.split("\n"):
        status = line[:2].strip()
        filepath = line[3:].strip()
        if status == "M" or status == "MM":
            print(f"    {C.YELLOW}✎{C.RESET}  {filepath}")
        elif status == "A" or status == "??":
            print(f"    {C.GREEN}+{C.RESET}  {filepath}")
        elif status == "D":
            print(f"    {C.RED}✕{C.RESET}  {filepath}")
        else:
            print(f"    {C.BLUE}~{C.RESET}  {filepath}")

    out, err, code = run_git("commit", "-m", message)
    if code == 0:
        # Get the short hash
        hash_out, _, _ = run_git("log", "--oneline", "-1")
        print(f"\n  {C.GREEN}✓ Committed: {hash_out}{C.RESET}")
    else:
        print(f"\n  {C.RED}✗ Commit failed: {err}{C.RESET}")


def cmd_files():
    """Show all tracked files organized by category."""
    print(f"\n{C.BOLD}📁 TRACKED FILES BY CATEGORY{C.RESET}")
    print(f"{'─'*55}")

    stdout, _, _ = run_git("ls-files")
    if not stdout:
        print(f"  {C.DIM}No tracked files{C.RESET}")
        return

    files = stdout.strip().split("\n")
    by_category = {}
    for f in files:
        cat = categorize_file(f)
        by_category.setdefault(cat, []).append(f)

    for cat in sorted(by_category.keys()):
        file_list = by_category[cat]
        print(f"\n  {C.CYAN}{cat} ({len(file_list)}):{C.RESET}")
        for f in sorted(file_list):
            # Show file size
            full_path = os.path.join(PROJECT_DIR, f)
            try:
                size = os.path.getsize(full_path)
                if size > 1024:
                    size_str = f"{size/1024:.1f} KB"
                else:
                    size_str = f"{size} B"
            except OSError:
                size_str = "?"
            print(f"    {f}  {C.DIM}({size_str}){C.RESET}")

    print(f"\n  {C.BOLD}Total: {len(files)} tracked files{C.RESET}")


def cmd_summary():
    """Quick summary: status + last few commits."""
    cmd_status()
    print()
    cmd_log(5)


def cmd_undo():
    """Undo the last commit (keep changes as uncommitted)."""
    print(f"\n{C.BOLD}⏪ UNDO LAST COMMIT{C.RESET}")
    print(f"{'─'*55}")

    # Show what will be undone
    log_out, _, _ = run_git("log", "--oneline", "-1")
    if not log_out:
        print(f"  {C.RED}✗ No commits to undo{C.RESET}")
        return

    print(f"  Undoing: {C.YELLOW}{log_out}{C.RESET}")
    confirm = input(f"  {C.RED}Are you sure? (y/n): {C.RESET}").strip().lower()
    if confirm != "y":
        print(f"  Cancelled.")
        return

    out, err, code = run_git("reset", "--soft", "HEAD~1")
    if code == 0:
        print(f"  {C.GREEN}✓ Commit undone — changes are now unstaged{C.RESET}")
    else:
        print(f"  {C.RED}✗ Failed: {err}{C.RESET}")


def cmd_backup():
    """Create a tagged backup point."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag_name = f"backup_{timestamp}"

    # First commit any pending changes
    run_git("add", "-A")
    stdout, _, _ = run_git("status", "--porcelain")
    if stdout:
        run_git("commit", "-m", f"Backup checkpoint — {timestamp}")

    out, err, code = run_git("tag", tag_name)
    if code == 0:
        print(f"\n  {C.GREEN}✓ Backup created: {C.BOLD}{tag_name}{C.RESET}")
        print(f"  {C.DIM}Restore with: git checkout {tag_name}{C.RESET}")
    else:
        print(f"\n  {C.RED}✗ Tag failed: {err}{C.RESET}")


def cmd_backups():
    """List all backup tags."""
    print(f"\n{C.BOLD}🏷️  BACKUP POINTS{C.RESET}")
    print(f"{'─'*55}")

    stdout, _, _ = run_git("tag", "-l", "backup_*", "--sort=-creatordate")
    if not stdout:
        print(f"  {C.DIM}No backups yet. Use 'backup' to create one.{C.RESET}")
        return

    for tag in stdout.split("\n"):
        # Get the commit info for this tag
        info, _, _ = run_git("log", "-1", "--format=%ar — %s", tag.strip())
        print(f"  {C.YELLOW}{tag.strip()}{C.RESET}  {C.DIM}{info}{C.RESET}")


# =============================================================================
# INTERACTIVE MENU
# =============================================================================

def interactive_menu():
    """Show interactive menu."""
    banner()
    while True:
        print(f"""
{C.BOLD}  Commands:{C.RESET}
    {C.CYAN}1{C.RESET}  Status        — See what files changed
    {C.CYAN}2{C.RESET}  Diff          — See actual code changes
    {C.CYAN}3{C.RESET}  Commit        — Save all changes to Git
    {C.CYAN}4{C.RESET}  Log           — View recent history
    {C.CYAN}5{C.RESET}  Log (detail)  — History with files changed
    {C.CYAN}6{C.RESET}  Files         — All tracked files by category
    {C.CYAN}7{C.RESET}  Backup        — Create a restore point
    {C.CYAN}8{C.RESET}  Backups       — List all restore points
    {C.CYAN}9{C.RESET}  Undo          — Undo last commit
    {C.CYAN}0{C.RESET}  Exit
""")
        choice = input(f"  {C.BOLD}Choose (0-9): {C.RESET}").strip()

        if choice == "1":
            cmd_status()
        elif choice == "2":
            cmd_diff()
        elif choice == "3":
            msg = input(f"  {C.DIM}Commit message (Enter for auto): {C.RESET}").strip()
            cmd_commit(msg if msg else None)
        elif choice == "4":
            cmd_log()
        elif choice == "5":
            cmd_log_detailed()
        elif choice == "6":
            cmd_files()
        elif choice == "7":
            cmd_backup()
        elif choice == "8":
            cmd_backups()
        elif choice == "9":
            cmd_undo()
        elif choice == "0" or choice.lower() == "q":
            print(f"\n  {C.GREEN}Goodbye!{C.RESET}\n")
            break
        else:
            print(f"  {C.RED}Invalid choice{C.RESET}")


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        interactive_menu()
    elif args[0] == "status":
        cmd_status()
    elif args[0] == "diff":
        cmd_diff()
    elif args[0] == "commit":
        msg = " ".join(args[1:]) if len(args) > 1 else None
        cmd_commit(msg)
    elif args[0] == "log":
        count = int(args[1]) if len(args) > 1 else 15
        cmd_log(count)
    elif args[0] == "detail":
        cmd_log_detailed()
    elif args[0] == "files":
        cmd_files()
    elif args[0] == "backup":
        cmd_backup()
    elif args[0] == "backups":
        cmd_backups()
    elif args[0] == "undo":
        cmd_undo()
    elif args[0] == "summary":
        cmd_summary()
    else:
        print(f"Unknown command: {args[0]}")
        print("Available: status, diff, commit, log, detail, files, backup, backups, undo, summary")
