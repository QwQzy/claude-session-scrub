#!/usr/bin/env python3
"""scrub-session-images — 流式清理 Claude Code 会话 JSONL 中的非法图片块。

背景
----
Claude Code 的 Read 工具按文件扩展名判断 media_type。被读取的文件扩展名是
.png 但实际内容不是合法图片时，它会把非法二进制塞进 tool_result.image，导致
Anthropic API 返回 400: unsupported image。坏数据永久留在会话 JSONL 里，后续
每个请求都会重放失败。

本脚本逐行扫描 JSONL，把 {"type": "image", "source": {...}} 块原地替换为
{"type": "text", "text": "[image removed: was <media_type>]"}，然后原子写回。
未修改的行按原始字节保留，不做重新序列化。

用法
----
    ./scrub_session_images.py session.jsonl
    ./scrub_session_images.py --dry-run sessions/*.jsonl
    ./scrub_session_images.py --no-backup session.jsonl

退出码
------
    0  成功（可能修改，也可能无需修改）
    1  至少一个文件处理失败
    2  参数错误
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Any

__version__ = "1.0.0"

IMAGE_TYPE = "image"
TEXT_TYPE = "text"
# JSON 里 type 的值一定是带引号的 "image"，用它做精确预筛。
IMAGE_MARKER = b'"image"'
# 预扫描和文件复制的分块大小。
CHUNK = 8 << 20  # 8 MiB


@dataclass
class FileStats:
    path: str
    lines_total: int = 0
    lines_changed: int = 0
    images_removed: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    backup_path: str | None = None
    skipped_no_marker: bool = False
    error: str | None = None


def scrub(node: Any) -> int:
    """递归替换 node 中的 image 块，返回移除的块数。"""
    removed = 0
    if isinstance(node, dict):
        if node.get("type") == IMAGE_TYPE and isinstance(node.get("source"), dict):
            media_type = node["source"].get("media_type", "?")
            node.clear()
            node["type"] = TEXT_TYPE
            node["text"] = f"[image removed: was {media_type}]"
            removed += 1
        for value in node.values():
            removed += scrub(value)
    elif isinstance(node, list):
        for value in node:
            removed += scrub(value)
    return removed


def split_eol(line: bytes) -> tuple[bytes, bytes]:
    """拆分行尾，保留原始换行风格（\\n 或 \\r\\n）。"""
    if line.endswith(b"\r\n"):
        return line[:-2], b"\r\n"
    if line.endswith(b"\n"):
        return line[:-1], b"\n"
    return line, b""


def maybe_contains_image(path: str) -> bool:
    """快速预扫描：文件里是否可能出现 image 块。

    只看是否出现 JSON 字符串 "image"。命中返回 True（可能误判，交给后续精确解析）。
    跨块边界用 carry 处理，避免特征串被切断漏检。
    """
    carry = b""
    overlap = len(IMAGE_MARKER) - 1
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                return False
            buf = carry + chunk
            if IMAGE_MARKER in buf:
                return True
            carry = buf[-overlap:] if overlap else b""


def make_backup(path: str) -> str:
    """创建备份。.bak 已存在时使用带时间戳的新名字，不覆盖旧备份。"""
    base = path + ".bak"
    if not os.path.exists(base):
        shutil.copy2(path, base)
        return base
    alt = f"{path}.bak.{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, alt)
    return alt


def _dry_run_scan(path: str, stats: FileStats) -> None:
    """只统计，不写文件。"""
    with open(path, "rb") as src:
        for raw_line in src:
            stats.lines_total += 1
            if IMAGE_MARKER not in raw_line:
                continue
            body, _ = split_eol(raw_line)
            try:
                obj = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            removed = scrub(obj)
            if removed:
                stats.lines_changed += 1
                stats.images_removed += removed
    stats.bytes_out = stats.bytes_in


def _rewrite(path: str, stats: FileStats, *, backup: bool) -> FileStats:
    """流式重写到同目录临时文件，完成后原子替换原文件。"""
    dirpath = os.path.dirname(os.path.abspath(path))
    src_stat = os.stat(path)
    fd, tmp_path = tempfile.mkstemp(dir=dirpath, prefix=".scrub-", suffix=".tmp")
    tmp = os.fdopen(fd, "wb")
    committed = False
    try:
        with open(path, "rb") as src:
            for raw_line in src:
                stats.lines_total += 1
                # 快路径：不含精确标记，原样写回（未修改的行保持字节一致）。
                if IMAGE_MARKER not in raw_line:
                    tmp.write(raw_line)
                    continue
                body, eol = split_eol(raw_line)
                try:
                    obj = json.loads(body)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    tmp.write(raw_line)
                    continue
                removed = scrub(obj)
                if removed == 0:
                    tmp.write(raw_line)
                    continue
                stats.lines_changed += 1
                stats.images_removed += removed
                out = json.dumps(
                    obj, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
                tmp.write(out + eol)

        tmp.flush()
        tmp.close()
        tmp = None

        if stats.lines_changed == 0:
            # 预扫描命中但实际没有可移除的块（"image" 只出现在文本里）。
            os.unlink(tmp_path)
            committed = True
            stats.bytes_out = stats.bytes_in
        else:
            try:
                os.chmod(tmp_path, stat.S_IMODE(src_stat.st_mode))
            except OSError:
                pass
            if backup:
                stats.backup_path = make_backup(path)
            os.replace(tmp_path, path)  # 同目录内原子替换
            committed = True
            stats.bytes_out = os.path.getsize(path)
    finally:
        if tmp is not None:
            try:
                tmp.close()
            except OSError:
                pass
        if not committed and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    return stats


def scrub_file(path: str, *, dry_run: bool, backup: bool) -> FileStats:
    stats = FileStats(path=path)
    if not os.path.isfile(path):
        stats.error = "not a regular file"
        return stats

    stats.bytes_in = os.path.getsize(path)

    # 预扫描：没有 "image" 标记的文件直接跳过，避免对大文件做无谓复制。
    if not maybe_contains_image(path):
        stats.skipped_no_marker = True
        stats.bytes_out = stats.bytes_in
        return stats

    if dry_run:
        _dry_run_scan(path, stats)
        return stats

    return _rewrite(path, stats, backup=backup)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scrub-session-images",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Remove invalid image blocks from Claude Code session JSONL files.\n"
            "\n"
            "Fixes the HTTP 400 \"unsupported image\" error caused by poisoned\n"
            "tool_result.image data written by the Read tool. The script streams\n"
            "input line by line, so multi-GB session files are safe to process."
        ),
        epilog=(
            "examples:\n"
            "  # Safe preview — reports what would change, writes nothing\n"
            "  scrub-session-images --dry-run ~/.claude/projects/*/*.jsonl\n"
            "\n"
            "  # Apply fixes; a .bak backup is created for each modified file\n"
            "  scrub-session-images ~/.claude/projects/*/*.jsonl\n"
            "\n"
            "  # Resume the repaired session\n"
            "  claude --resume <session-id>\n"
        ),
    )

    parser.add_argument(
        "files",
        nargs="+",
        metavar="FILE",
        help="One or more .jsonl session files. Shell globs are expanded "
             "by your shell, so ~/.claude/projects/*/*.jsonl works as-is.",
    )

    safety = parser.add_argument_group("safety")
    safety.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be changed without writing anything. "
             "Recommended for the first run on unfamiliar files.",
    )
    safety.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip the .bak backup. A backup IS created by default; use this "
             "only if you already copied the file or are short on disk space.",
    )

    output = parser.add_argument_group("output")
    output.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Only print files that were modified or failed. Hides "
             "\"[skip]\" (no image marker) and \"[ok]\" (nothing to fix) lines.",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    args = parser.parse_args(argv)

    any_error = False
    for path in args.files:
        stats = scrub_file(path, dry_run=args.dry_run, backup=not args.no_backup)
        if stats.error:
            any_error = True
            print(f"[err]  {path}: {stats.error}", file=sys.stderr)
            continue
        if stats.skipped_no_marker:
            if not args.quiet:
                print(f"[skip] {path}  (no image marker, untouched)")
            continue
        if stats.lines_changed == 0:
            if not args.quiet:
                print(f"[ok]   {path}  (no image blocks, untouched)")
            continue
        prefix = "[dry]" if args.dry_run else "[fix]"
        verb = "would" if args.dry_run else "did"
        backup_note = f"  backup={stats.backup_path}" if stats.backup_path else ""
        print(
            f"{prefix}  {path}  "
            f"lines={stats.lines_total} {verb} change {stats.lines_changed}, "
            f"images removed={stats.images_removed}{backup_note}"
        )

    return 1 if any_error else 0


if __name__ == "__main__":
    sys.exit(main())