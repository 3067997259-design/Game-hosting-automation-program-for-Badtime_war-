"""检查文档登记、唯一权威、链接、生成标记和 Git 跟踪状态。"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = ROOT / "docs" / "document_registry.json"
ALLOWED_STATUSES = {
    "canonical",
    "candidate",
    "migration-source",
    "generated",
    "draft",
    "decision-ledger",
    "mixed",
    "historical",
    "superseded",
    "scoped-current",
}
LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
GENERATED_BANNER = "<!-- GENERATED FILE:"


@dataclass(frozen=True)
class Finding:
    """单条治理检查结果。"""

    severity: str
    code: str
    path: str
    detail: str


def relative(path: Path) -> str:
    """返回使用正斜线的仓库相对路径。"""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def load_registry(path: Path) -> Dict[str, Any]:
    """加载文档登记表。"""
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def registered_inventory() -> Set[str]:
    """枚举应由总登记表直接覆盖的人工文稿。"""
    result: Set[str] = set()
    for pattern in ("*.md", "*.docx"):
        for path in ROOT.glob(pattern):
            if path.name.startswith("~$"):
                continue
            result.add(relative(path))
    for path in (ROOT / "docs").glob("*.md"):
        result.add(relative(path))
    for path in (ROOT / "docs" / "audits").glob("*.md"):
        result.add(relative(path))
    optional_subproject = ROOT / "deepseek-cursor-proxy" / "README.md"
    if optional_subproject.exists():
        result.add(relative(optional_subproject))
    return result


def check_registry(registry: Dict[str, Any]) -> List[Finding]:
    """检查登记表结构、文件存在性和唯一权威。"""
    findings: List[Finding] = []
    profiles = set(registry.get("profiles", []))
    documents = registry.get("documents", [])
    if not isinstance(documents, list):
        return [Finding("ERROR", "REGISTRY_SCHEMA", "", "documents 必须是数组")]

    paths: List[str] = []
    authorities: Dict[Tuple[str, str], str] = {}
    status_by_path: Dict[str, str] = {}
    for document in documents:
        path = str(document.get("path", ""))
        status = str(document.get("status", ""))
        paths.append(path)
        status_by_path[path] = status
        if status not in ALLOWED_STATUSES:
            findings.append(Finding("ERROR", "UNKNOWN_STATUS", path, status))
        for profile in document.get("profiles", []):
            if profile not in profiles:
                findings.append(Finding("ERROR", "UNKNOWN_PROFILE", path, str(profile)))
        target = ROOT / path
        if not target.exists():
            findings.append(Finding("ERROR", "MISSING_REGISTERED_FILE", path, "文件不存在"))
        if status == "canonical":
            for profile in document.get("profiles", []):
                for topic in document.get("authority_for", []):
                    key = (str(profile), str(topic))
                    previous = authorities.get(key)
                    if previous is not None:
                        findings.append(
                            Finding(
                                "ERROR",
                                "DUPLICATE_AUTHORITY",
                                path,
                                "{0} 已由 {1} 声明".format(key, previous),
                            )
                        )
                    authorities[key] = path

    if len(paths) != len(set(paths)):
        findings.append(Finding("ERROR", "DUPLICATE_PATH", "", "登记表存在重复 path"))

    missing = registered_inventory() - set(paths)
    for path in sorted(missing):
        findings.append(Finding("ERROR", "UNREGISTERED_DOCUMENT", path, "人工文稿未登记"))

    for path, status in status_by_path.items():
        if status != "generated" or not Path(path).name.startswith("complete.generated"):
            continue
        text = (ROOT / path).read_text(encoding="utf-8")
        if not text.startswith(GENERATED_BANNER):
            findings.append(
                Finding("ERROR", "MISSING_GENERATED_BANNER", path, "生成文件缺少禁止编辑标记")
            )
    return findings


def iter_markdown_links(path: Path) -> Iterable[str]:
    """枚举 Markdown 中的本地链接目标。"""
    text = path.read_text(encoding="utf-8-sig")
    for match in LINK_PATTERN.finditer(text):
        target = match.group(1).strip().split()[0].strip("<>")
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        yield unquote(target.split("#", 1)[0])


def check_links(registry: Dict[str, Any]) -> List[Finding]:
    """检查登记文稿中的相对 Markdown 链接。"""
    findings: List[Finding] = []
    link_documents = list(registry.get("documents", []))
    for manifest_path in (
        ROOT / "docs" / "handbook" / "manifest.json",
        ROOT / "docs" / "legacy" / "manifest.json",
    ):
        if not manifest_path.exists():
            continue
        manifest = load_registry(manifest_path)
        for module in manifest.get("modules", []):
            link_documents.append(
                {
                    "path": relative(manifest_path.parent / str(module["path"])),
                    "status": "canonical",
                }
            )

    for document in link_documents:
        path_text = str(document.get("path", ""))
        if path_text.startswith("docs/archive/"):
            continue
        path = ROOT / path_text
        if path.suffix.lower() != ".md" or not path.exists():
            continue
        for target in iter_markdown_links(path):
            if not target:
                continue
            resolved = (path.parent / target).resolve()
            if resolved.exists():
                continue
            severity = "ERROR" if document.get("status") == "canonical" else "WARN"
            findings.append(
                Finding(severity, "BROKEN_LOCAL_LINK", relative(path), target)
            )
    return findings


def git_tracked_paths() -> Optional[Set[str]]:
    """读取 Git 已跟踪路径；Git 不可用时返回 None。"""
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=false", "ls-files"],
            cwd=str(ROOT),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return {line.replace("\\", "/") for line in result.stdout.splitlines() if line}


def check_tracking(registry: Dict[str, Any]) -> List[Finding]:
    """报告未被 Git 跟踪的重要文档，不自动修改索引。"""
    tracked = git_tracked_paths()
    if tracked is None:
        return [Finding("WARN", "GIT_UNAVAILABLE", "", "无法读取 Git 跟踪状态")]
    findings: List[Finding] = []
    important = {"canonical", "candidate", "migration-source", "decision-ledger", "scoped-current"}
    for document in registry.get("documents", []):
        path = str(document.get("path", "")).replace("\\", "/")
        if document.get("status") in important and path not in tracked:
            findings.append(Finding("WARN", "UNTRACKED_IMPORTANT_DOC", path, "尚未纳入 Git"))
    return findings


def run_checks(registry_path: Path) -> List[Finding]:
    """运行全部治理检查。"""
    registry = load_registry(registry_path)
    return check_registry(registry) + check_links(registry) + check_tracking(registry)


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """命令行入口。"""
    args = build_parser().parse_args(argv)
    try:
        findings = run_checks(args.registry.resolve())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print("❌ {0}".format(error), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([asdict(item) for item in findings], ensure_ascii=False, indent=2))
    else:
        for finding in findings:
            print(
                "[{0}] {1} {2}: {3}".format(
                    finding.severity, finding.code, finding.path or "-", finding.detail
                )
            )
        counts = {
            severity: sum(1 for item in findings if item.severity == severity)
            for severity in ("ERROR", "WARN")
        }
        print("ERROR: {0} | WARN: {1}".format(counts["ERROR"], counts["WARN"]))
    return 1 if any(item.severity == "ERROR" for item in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
