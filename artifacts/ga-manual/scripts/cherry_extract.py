"""从 CherryStudio agent 会话还原手册草稿的每一步版本（只读源，写到 scratchpad）。"""
import json
import sys
from pathlib import Path

src = Path(sys.argv[1])
out_dir = Path(sys.argv[2])
out_dir.mkdir(parents=True, exist_ok=True)

step = 0
current = None
with src.open(encoding="utf-8") as fh:
    for lineno, line in enumerate(fh, 1):
        obj = json.loads(line)
        res = obj.get("toolUseResult")
        if not isinstance(res, dict):
            continue
        fp = res.get("filePath") or ""
        if "manual_draft_v0.1.md" not in fp:
            continue
        step += 1
        if "content" in res and "oldString" not in res:  # Write
            before = res.get("originalFile")
            after = res["content"]
        else:  # Edit
            before = res.get("originalFile")
            old, new = res["oldString"], res["newString"]
            if before is None:
                before = current
            assert before is not None and before.count(old) == 1, (lineno, before is None)
            after = before.replace(old, new, 1)
        if current is not None and before is not None and before != current:
            print(f"  WARNING step {step}: originalFile differs from previous result")
        current = after
        (out_dir / f"step{step}.md").write_text(after, encoding="utf-8", newline="")
        print(f"step {step} L{lineno} {obj.get('timestamp')} lines={after.count(chr(10))} chars={len(after)} userModified={res.get('userModified')}")

(out_dir / "cherry_final.md").write_text(current, encoding="utf-8", newline="")
