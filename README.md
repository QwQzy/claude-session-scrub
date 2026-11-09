# claude-session-scrub

**English** · [简体中文](README.zh-CN.md)

![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![Zero dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

Remove invalid image blocks from Claude Code session files and unstick dead sessions.

---

## The problem

You try to resume a Claude Code session and every request fails with:

```
API Error: 400 ... content.0.tool_result.image[0]: You have uploaded an
unsupported image. Please make sure your image is valid and has one of the
following formats: webp, png, jpeg, and gif.
```

The session is stuck. No amount of retrying helps, because the bad data is **persisted in the session file itself**.

### Why it happens

Claude Code's `Read` tool decides whether a file is an image **by its extension**, not by its contents. If you read a file named `chart.png` that is actually SVG, PDF, or something else, the raw bytes get stuffed into a `tool_result.image` block with a wrong `media_type`. The Anthropic API rejects them, and those poisoned blocks are permanently written to the session JSONL:

```
~/.claude/projects/<encoded-cwd>/<session-id>.jsonl
```

Every subsequent request replays the same invalid image data. The session is dead until you remove it.

## The fix

One command:

```bash
./scrub_session_images.py ~/.claude/projects/<encoded-cwd>/<session-id>.jsonl
```

Then resume the session normally:

```bash
claude --resume <session-id>
```

## Quick start

```bash
# 1. Download
curl -O https://raw.githubusercontent.com/qwqzy/claude-session-scrub/main/scrub_session_images.py
chmod +x scrub_session_images.py

# 2. Preview what would change (writes nothing)
./scrub_session_images.py --dry-run ~/.claude/projects/*/*.jsonl

# 3. Apply the fix
./scrub_session_images.py ~/.claude/projects/*/*.jsonl
```

## Installation

The script is a **single file with zero dependencies** — only the Python 3.9+ standard library is required.

**Option A — download directly:**

```bash
curl -O https://raw.githubusercontent.com/qwqzy/claude-session-scrub/main/scrub_session_images.py
chmod +x scrub_session_images.py
```

**Option B — clone the repo:**

```bash
git clone https://github.com/qwqzy/claude-session-scrub.git
cd claude-session-scrub
chmod +x scrub_session_images.py
```

No `pip install`, no virtualenv, no Node.js. It runs wherever `python3` exists — which on Linux and macOS is everywhere.

## Usage

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

### Example output

```
[dry]  /home/me/.claude/projects/-home-me-code-app/session.jsonl  lines=4821 would change 7, images removed=7
[fix]  /home/me/.claude/projects/-home-me-code-app/session.jsonl  lines=4821 did change 7, images removed=7  backup=/home/me/.claude/projects/-home-me-code-app/session.jsonl.bak
[skip] /home/me/.claude/projects/-home-me-code-app/other.jsonl  (no image marker, untouched)
[ok]   /home/me/.claude/projects/-home-me-code-app/old.jsonl  (no image blocks, untouched)
```

## How it works

The script does four things, in this order:

1. **Pre-scan.** Before touching anything, it scans the file in 8 MiB chunks looking for the byte sequence `"image"`. If the file contains no such marker, it is skipped entirely — **zero bytes written**. This is what makes it safe to run against a directory of dozens of multi-GB sessions.

2. **Stream, don't buffer.** The file is processed line by line. Memory usage is `O(largest single line)`, not `O(file size)`. A 5 GB session file is fine.

3. **Atomic replacement.** Modified content is written to a temporary file in the same directory, then swapped in with `os.replace()` — an atomic operation on both Linux and macOS. If the process is killed mid-write, the original file is untouched.

4. **Byte-exact preservation of unmodified lines.** Lines that don't contain a `"image"` marker are written back verbatim. Only lines that actually contained an image block are re-serialized.

A backup (`<file>.jsonl.bak`) is created before any modification, unless `--no-backup` is passed or the backup already exists (in which case a timestamped backup is created instead of overwriting).

## Safety

**This tool removes all `image` blocks, including valid ones.**

That is a deliberate design choice, not a limitation. Once a session contains a single invalid image block, the entire session is unusable — the API rejects every request that replays it. Trying to distinguish "valid" images from "invalid" ones requires base64-decoding every image and checking magic bytes, which adds complexity and failure modes to a recovery tool.

The philosophy is **recovery first, precision second**: get the session working again as fast as possible, with a clear textual placeholder (`[image removed: was image/png]`) left in place of each removed block so you can see what was lost.

If you need selective removal — only the invalid ones, keep the valid ones — use a more surgical tool like [`session_image_surgeon.py`](https://github.com/). For the common case (session is dead, just make it work), this script is faster and simpler.

**Your data is backed up.** Before any modification, a `.bak` file is created. If a `.bak` already exists, a timestamped backup is created instead of overwriting it, so you never lose the original.

**Unmodified lines are preserved byte-for-byte.** The script does not reformat or re-serialize lines it doesn't need to change.

## FAQ

**Does it work on Windows?**
Yes, but `os.replace()` on Windows fails if the destination file is open by another process. Close Claude Code before running the script.

**What if I have a multi-GB session file?**
That's the primary use case. The script streams input line by line and only rewrites lines that contain image blocks. A clean 5 GB file takes a few seconds and writes zero bytes.

**Will it corrupt my session file?**
No. Modifications go to a temporary file first, then are atomically swapped in. If anything fails mid-write, the original file is untouched. A backup is also created before any modification.

**Can I undo the change?**
Yes — the `.bak` file contains the exact original. `mv session.jsonl.bak session.jsonl` restores it.

**Does it remove valid images too?**
Yes. See [Safety](#safety). This is intentional — the tool optimizes for "session works again", not "preserve every image".

**Why not just start a new session?**
Starting over loses the entire conversation context. For a session you've been working in for hours, that's a much bigger loss than the images.

## Alternatives

| Tool | Streaming | Zero deps | Atomic writes | Selective removal |
| :--- | :---: | :---: | :---: | :---: |
| **claude-session-scrub** | ✅ | ✅ | ✅ | ❌ |
| [conclear](https://www.npmjs.com/package/conclear) | ❌ | ❌ (Node) | ❌ | ✅ |
| [@image-cascade/cli](https://www.npmjs.com/package/@image-cascade/cli) | ✅ | ❌ (Node) | ✅ | ❌ |
| [image-strip](https://github.com/) | ❌ | ❌ | ❌ | ❌ |

If you need a GUI, preview thumbnails, or selective removal, use `conclear`. If you want a zero-dependency single file that streams through multi-GB files, this is it.

## License

MIT

