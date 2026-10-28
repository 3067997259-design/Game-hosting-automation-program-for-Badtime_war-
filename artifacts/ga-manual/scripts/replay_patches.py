"""把 Codex 会话中针对手册的 apply_patch 依时间顺序重放到 CherryStudio 版草稿上（只读源）。

用法: python replay_patches.py <base.md> <out.md> <session1.jsonl> [...]
"""
import hashlib
import json
import re
import sys
from pathlib import Path

TARGET = "manual_draft_v0.1.md"


def js_string_at(code: str, start: int) -> tuple[str, int]:
    """解析 code[start] 处的 JS 字符串字面量，返回 (值, 结束位置)。"""
    quote = code[start]
    assert quote in "\"'`", code[start:start + 20]
    i = start + 1
    out = []
    while i < len(code):
        ch = code[i]
        if ch == "\\":
            nxt = code[i + 1]
            simple = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "\"": "\"", "'": "'",
                      "`": "`", "0": "\0", "b": "\b", "f": "\f", "v": "\v", "$": "$"}
            if nxt in simple:
                out.append(simple[nxt])
                i += 2
            elif nxt == "u":
                if code[i + 2] == "{":
                    end = code.index("}", i)
                    out.append(chr(int(code[i + 3:end], 16)))
                    i = end + 1
                else:
                    out.append(chr(int(code[i + 2:i + 6], 16)))
                    i += 6
            elif nxt == "x":
                out.append(chr(int(code[i + 2:i + 4], 16)))
                i += 4
            elif nxt == "\n":
                i += 2
            else:
                out.append(nxt)
                i += 2
        elif ch == quote:
            return "".join(out), i + 1
        else:
            out.append(ch)
            i += 1
    raise ValueError("unterminated string")


def extract_patches(code: str) -> list[str]:
    patches = []
    for m in re.finditer(r"apply_patch\(\s*", code):
        pos = m.end()
        if code[pos] in "\"'`":
            val, _ = js_string_at(code, pos)
            patches.append(val)
    return patches


def manual_chunks(patch: str) -> list[dict] | None:
    """返回补丁中对手册的 chunk 列表；没有则 None。"""
    lines = patch.split("\n")
    i = 0
    in_target = False
    chunks: list[dict] = []
    found = False
    cur = None
    while i < len(lines):
        line = lines[i]
        if line.startswith("*** "):
            if line.startswith("*** Update File:"):
                in_target = TARGET in line
                found = found or in_target
                cur = None
            elif line.startswith(("*** Add File:", "*** Delete File:")):
                in_target = False
                cur = None
            elif line.startswith("*** Move to:"):
                pass
            elif line.startswith("*** End of File"):
                if in_target and cur is not None:
                    cur["eof"] = True
            i += 1
            continue
        if in_target:
            if line.startswith("@@"):
                cur = {"ctx": line[2:].strip(), "old": [], "new": [], "eof": False}
                chunks.append(cur)
            else:
                if cur is None:
                    cur = {"ctx": "", "old": [], "new": [], "eof": False}
                    chunks.append(cur)
                if line.startswith("+"):
                    cur["new"].append(line[1:])
                elif line.startswith("-"):
                    cur["old"].append(line[1:])
                elif line.startswith(" "):
                    cur["old"].append(line[1:])
                    cur["new"].append(line[1:])
                elif line == "":
                    # 空行视为空上下文行
                    cur["old"].append("")
                    cur["new"].append("")
        i += 1
    return chunks if found else None


def norm_variants(s: str):
    yield s
    yield s.rstrip()
    yield s.strip()


def seek(lines: list[str], pattern: list[str], start: int) -> int:
    if not pattern:
        return start
    for level in range(3):
        def f(x: str) -> str:
            return x if level == 0 else (x.rstrip() if level == 1 else x.strip())
        pat = [f(p) for p in pattern]
        for idx in range(start, len(lines) - len(pattern) + 1):
            if all(f(lines[idx + k]) == pat[k] for k in range(len(pattern))):
                return idx
    return -1


def apply_chunks(text: str, chunks: list[dict]) -> str:
    lines = text.split("\n")
    pos = 0
    replacements = []
    for ch in chunks:
        if ch["ctx"]:
            idx = seek(lines, [ch["ctx"]], pos)
            if idx < 0:
                raise ValueError(f"context not found: {ch['ctx'][:60]}")
            pos = idx + 1
        old, new = ch["old"], ch["new"]
        # 去掉补丁末尾由分隔产生的空行
        while old and new and old[-1] == "" and new[-1] == "":
            old, new = old[:-1], new[:-1]
        idx = seek(lines, old, pos)
        if idx < 0:
            raise ValueError("old lines not found: " + " | ".join(old[:3])[:200])
        replacements.append((idx, len(old), new))
        pos = idx + len(old)
    for idx, n, new in sorted(replacements, reverse=True):
        lines[idx:idx + n] = new
    return "\n".join(lines)


def main() -> None:
    base = Path(sys.argv[1]).read_text(encoding="utf-8")
    out = Path(sys.argv[2])
    events = []
    for p in sys.argv[3:]:
        outputs = {}
        calls = []
        with open(p, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                if "apply_patch" not in line and "call_id" not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                pl = obj.get("payload", {}) or {}
                t = pl.get("type")
                if t in ("function_call", "custom_tool_call"):
                    args = pl.get("arguments") or pl.get("input") or ""
                    code = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
                    if "apply_patch" in code and TARGET in code:
                        calls.append((obj.get("timestamp"), lineno, pl.get("call_id"), code))
                elif t in ("function_call_output", "custom_tool_call_output"):
                    o = pl.get("output")
                    outputs[pl.get("call_id")] = o if isinstance(o, str) else json.dumps(o, ensure_ascii=False)
        for ts, lineno, cid, code in calls:
            events.append((ts, Path(p).name[:30], lineno, code, outputs.get(cid, "")))
    events.sort()
    text = base
    for ts, name, lineno, code, output in events:
        ok = "Script completed" in output
        failed = "Script failed" in output or "verification fail" in output
        for patch in extract_patches(code):
            chunks = manual_chunks(patch)
            if chunks is None:
                continue
            status = "OK" if ok else ("FAILED" if failed else "??")
            if status == "FAILED":
                print(f"{ts} {name} L{lineno} SKIP (tool reported failure): {output[:160]!r}")
                continue
            try:
                text = apply_chunks(text, chunks)
                print(f"{ts} {name} L{lineno} applied {len(chunks)} chunks [{status}] lines={text.count(chr(10))}")
            except ValueError as exc:
                print(f"{ts} {name} L{lineno} APPLY ERROR [{status}] {exc}; out={output[:160]!r}")
    out.write_text(text, encoding="utf-8", newline="")
    print("sha256", hashlib.sha256(text.encode("utf-8")).hexdigest(), "chars", len(text))


main()
