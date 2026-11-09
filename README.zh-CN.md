# claude-session-scrub

[English](README.md) · **简体中文**

![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![Zero dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

清理 Claude Code 会话文件中的非法图片块，让卡死的会话重新可用。

---

## 问题

你尝试恢复一个 Claude Code 会话，但每个请求都失败：

```
API Error: 400 ... content.0.tool_result.image[0]: You have uploaded an
unsupported image. Please make sure your image is valid and has one of the
following formats: webp, png, jpeg, and gif.
```

会话卡死了。重试多少次都没用，因为坏数据**已经写进了会话文件本身**。

### 为什么会这样

Claude Code 的 `Read` 工具**按扩展名判断文件是不是图片**，而不是检查文件内容。如果你读了一个叫 `chart.png` 但实际是 SVG、PDF 或其他格式的文件，它会把原始字节塞进一个 `tool_result.image` 块，并附上错误的 `media_type`。Anthropic API 会拒绝这些数据，而这些被污染的块会被永久写入会话 JSONL：

```
~/.claude/projects/<编码后的工作目录>/<会话ID>.jsonl
```

之后每个请求都会重放同样的非法图片数据。在你删掉它之前，这个会话是死的。

## 修复

一条命令：

```bash
./scrub_session_images.py ~/.claude/projects/<编码后的工作目录>/<会话ID>.jsonl
```

然后正常恢复会话：

```bash
claude --resume <会话ID>
```

## 快速开始

```bash
# 1. 下载
curl -O https://raw.githubusercontent.com/qwqzy/claude-session-scrub/main/scrub_session_images.py
chmod +x scrub_session_images.py

# 2. 预览会改什么（不写文件）
./scrub_session_images.py --dry-run ~/.claude/projects/*/*.jsonl

# 3. 实际修复
./scrub_session_images.py ~/.claude/projects/*/*.jsonl
```

## 安装

脚本是**单文件、零依赖**的——只需要 Python 3.9+ 标准库，不需要任何第三方包。

**方式 A —— 直接下载：**

```bash
curl -O https://raw.githubusercontent.com/qwqzy/claude-session-scrub/main/scrub_session_images.py
chmod +x scrub_session_images.py
```

**方式 B —— 克隆仓库：**

```bash
git clone https://github.com/qwqzy/claude-session-scrub.git
cd claude-session-scrub
chmod +x scrub_session_images.py
```

不需要 `pip install`，不需要虚拟环境，不需要 Node.js。只要有 `python3` 就能跑——Linux 和 macOS 上到处都有。

## 用法

```
usage: scrub-session-images [-h] [--dry-run] [--no-backup] [-q] [--version]
                            FILE [FILE ...]

Remove invalid image blocks from Claude Code session JSONL files.

Fixes the HTTP 400 "unsupported image" error caused by poisoned
tool_result.image data written by the Read tool. The script streams
input line by line, so multi-GB session files are safe to process.

positional arguments:
  FILE               One or more .jsonl session files. Shell globs are
                     expanded by your shell, so ~/.claude/projects/*/*.jsonl
                     works as-is.

safety:
  --dry-run          Report what would be changed without writing anything.
                     Recommended for the first run on unfamiliar files.
  --no-backup        Skip the .bak backup. A backup IS created by default; use
                     this only if you already copied the file or are short on
                     disk space.

output:
  -q, --quiet        Only print files that were modified or failed. Hides
                     "[skip]" (no image marker) and "[ok]" (nothing to fix)
                     lines.
```

### 输出示例

```
[dry]  /home/me/.claude/projects/-home-me-code-app/session.jsonl  lines=4821 would change 7, images removed=7
[fix]  /home/me/.claude/projects/-home-me-code-app/session.jsonl  lines=4821 did change 7, images removed=7  backup=/home/me/.claude/projects/-home-me-code-app/session.jsonl.bak
[skip] /home/me/.claude/projects/-home-me-code-app/other.jsonl  (no image marker, untouched)
[ok]   /home/me/.claude/projects/-home-me-code-app/old.jsonl  (no image blocks, untouched)
```

## 工作原理

脚本按顺序做四件事：

1. **预扫描。** 在动任何东西之前，先以 8 MiB 为块扫描文件，查找字节序列 `"image"`。如果整个文件没有这个标记，直接跳过——**一个字节都不写**。这就是为什么它可以安全地对一个包含几十个几 GB 会话的目录批量运行。

2. **流式处理，不缓冲。** 文件逐行处理，内存占用是 `O(最大单行)`，不是 `O(文件大小)`。5 GB 的会话文件也没问题。

3. **原子替换。** 修改后的内容先写入同目录的临时文件，然后用 `os.replace()` 换入——这在 Linux 和 macOS 上都是原子操作。如果进程中途被杀，原文件保持完好。

4. **未修改的行按字节保留。** 不含 `"image"` 标记的行原样写回。只有真正包含图片块的行才会被重新序列化。

在任何修改之前都会创建备份（`<文件>.jsonl.bak`），除非传了 `--no-backup`，或者备份已经存在（这时会创建一个带时间戳的新备份，而不是覆盖旧备份）。

## 安全性

**这个工具会移除所有 `image` 块，包括合法的图片。**

这是刻意的设计选择，不是缺陷。一旦会话里出现一个非法图片块，整个会话就不可用了——API 会拒绝每一个重放它的请求。试图区分"合法"和"非法"图片需要解码每个 base64 并检查 magic bytes，这会给一个恢复工具增加复杂度和失败面。

设计哲学是**恢复优先，精度其次**：尽快让会话重新工作，同时在每个被移除的块位置留下一个清晰的文本占位符（`[image removed: was image/png]`），让你知道丢了什么。

如果你需要选择性移除——只删非法的，保留合法的——用更精细的工具，比如 [`session_image_surgeon.py`](https://github.com/)。但对于常见场景（会话死了，只想让它重新工作），这个脚本更快也更简单。

**你的数据有备份。** 任何修改之前都会创建 `.bak` 文件。如果 `.bak` 已存在，会创建一个带时间戳的新备份，而不是覆盖旧备份，所以你永远不会丢失原始文件。

**未修改的行按字节保留。** 脚本不会重新格式化或重新序列化它不需要改动的行。

## 常见问题

**支持 Windows 吗？**
支持，但 Windows 上的 `os.replace()` 在目标文件被其他进程打开时会失败。运行脚本前请先关闭 Claude Code。

**几 GB 的会话文件能处理吗？**
这正是主要使用场景。脚本逐行流式读取，只重写包含图片块的行。一个干净的 5 GB 文件几秒钟就能处理完，而且不写入任何字节。

**会损坏我的会话文件吗？**
不会。修改先写入临时文件，然后原子换入。如果中途失败，原文件保持完好。任何修改之前也会创建备份。

**能撤销修改吗？**
能——`.bak` 文件包含完整的原始内容。`mv session.jsonl.bak session.jsonl` 就能恢复。

**合法图片也会被删除吗？**
会。见[安全性](#安全性)一节。这是有意为之——工具优化的是"让会话重新工作"，不是"保留每一张图片"。

**为什么不直接开新会话？**
开新会话会丢失整个对话上下文。对于一个你已经工作了几个小时的会话来说，这比丢失图片的损失大得多。

## 与其他工具对比

| 工具 | 流式 | 零依赖 | 原子写入 | 选择性移除 |
| :--- | :---: | :---: | :---: | :---: |
| **claude-session-scrub** | ✅ | ✅ | ✅ | ❌ |
| [conclear](https://www.npmjs.com/package/conclear) | ❌ | ❌ (Node) | ❌ | ✅ |
| [@image-cascade/cli](https://www.npmjs.com/package/@image-cascade/cli) | ✅ | ❌ (Node) | ✅ | ❌ |
| [image-strip](https://github.com/) | ❌ | ❌ | ❌ | ❌ |

如果你需要图形界面、缩略图预览或选择性移除，用 `conclear`。如果你想要一个零依赖、能流式处理几 GB 文件的单文件脚本，就是它。

## 许可证

MIT