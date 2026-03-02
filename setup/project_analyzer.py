"""Deterministic project analysis — port of analyze_project() from dark-factory.sh."""
from __future__ import annotations

import fnmatch
import sys
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """Structured result of deterministic project analysis."""

    language: str = ""
    framework: str = ""
    detected_strategy: str = "console"
    confidence: str = "low"
    description: str = ""
    build_cmd: str = ""
    test_cmd: str = ""
    run_cmd: str = ""
    base_image: str = "debian:bookworm-slim"
    required_tools: tuple[str, ...] = ()
    source_dirs: tuple[str, ...] = ("src/",)
    test_dirs: tuple[str, ...] = ("tests/",)
    has_web_server: bool = False
    has_database: bool = False
    has_iac: bool = False
    aws_services: tuple[str, ...] = ()
_CODE_EXTS = frozenset(
    ".sh .ts .tsx .js .jsx .py .go .rs .java .cs .cpp .c .h .rb .php "
    ".swift .kt .scala .zig .lua .ex .exs .hs .ml .clj .dart .r .pl .pm".split())
_EXCLUDE = frozenset(
    "tests test node_modules .git vendor dist __tests__ spec __pycache__ "
    ".tox .venv .mypy_cache coverage build target .next .nuxt venv env".split())
_EXT_LANG: dict[str, str] = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript",
    ".mjs": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".cs": "C#",
    ".rb": "Ruby", ".php": "PHP", ".cpp": "C++", ".c": "C", ".h": "C",
    ".swift": "Swift", ".kt": "Kotlin", ".scala": "Scala"}
_CONFIG_LANG: tuple[tuple[str, str], ...] = (
    ("Cargo.toml", "Rust"), ("go.mod", "Go"), ("pyproject.toml", "Python"),
    ("requirements.txt", "Python"), ("Pipfile", "Python"), ("pom.xml", "Java"),
    ("build.gradle", "Java"), ("Gemfile", "Ruby"), ("CMakeLists.txt", "C++"),
    ("package.json", "JavaScript"))
_FW_SIG: tuple[tuple[str, str, str], ...] = (
    ("package.json", "Next.js", '"next"'), ("package.json", "React", '"react"'),
    ("package.json", "Vue", '"vue"'), ("package.json", "Angular", '"@angular/core"'),
    ("package.json", "Express", '"express"'),
    ("pyproject.toml", "Django", "django"), ("requirements.txt", "Django", "django"),
    ("pyproject.toml", "FastAPI", "fastapi"), ("requirements.txt", "FastAPI", "fastapi"),
    ("pyproject.toml", "Flask", "flask"), ("requirements.txt", "Flask", "flask"),
    ("Cargo.toml", "Actix", "actix"), ("go.mod", "Gin", "gin-gonic"),
    ("pom.xml", "Spring Boot", "spring-boot"),
    ("build.gradle", "Spring Boot", "spring-boot"))
_WEB_FW = frozenset({
    "Next.js", "Express", "Django", "FastAPI", "Flask",
    "Actix", "Gin", "Spring Boot"})
_BASE_IMG: dict[str, str] = {
    "Rust": "rust:1.75-bookworm", "JavaScript": "node:22-bookworm",
    "TypeScript": "node:22-bookworm", "Go": "golang:1.22-bookworm",
    "Python": "python:3.12-bookworm",
    "C#": "mcr.microsoft.com/dotnet/sdk:8.0-bookworm-slim",
    "Java": "maven:3.9-eclipse-temurin-21-jammy",
    "C++": "gcc:13-bookworm", "C": "gcc:13-bookworm"}
_npm = ("npm run build", "npm test", "npm start", ("node", "npm"))
_CMD: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    "Django": ("", "python manage.py test", "python manage.py runserver", ("python", "pip")),
    "FastAPI": ("", "pytest", "uvicorn main:app", ("python", "pip")),
    "Flask": ("", "pytest", "flask run", ("python", "pip")),
    "Express": ("npm run build", "npm test", "node server.js", ("node", "npm")),
    "JavaScript": _npm, "TypeScript": _npm,
    "Python": ("", "pytest", "python -m app", ("python", "pip")),
    "Rust": ("cargo build --release", "cargo test", "cargo run", ("cargo",)),
    "Go": ("go build ./...", "go test ./...", "go run .", ("go",)),
    "Java": ("mvn package", "mvn test", "java -jar target/*.jar", ("java", "mvn")),
    "C#": ("dotnet build", "dotnet test", "dotnet run", ("dotnet",)),
    "Ruby": ("bundle install", "bundle exec rake test", "ruby app.rb", ("ruby", "bundle")),
    "C++": ("make", "make test", "./main", ("make", "gcc")),
    "C": ("make", "make test", "./main", ("make", "gcc"))}
_DB_KW = frozenset(
    "pg mysql mongodb sequelize prisma typeorm sqlalchemy diesel "
    "gorm psycopg2 pymongo redis sqlite3 knex".split())
_TEST_PATS = ("test_*", "*_test.*", "*.test.*", "*.spec.*", "*.bats")


def _rd(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _detect_language(root: Path) -> str:
    for cfg, lang in _CONFIG_LANG:
        if (root / cfg).is_file():
            if lang == "JavaScript" and (root / "tsconfig.json").is_file():
                return "TypeScript"
            return lang
    counts: Counter[str] = Counter()
    for f in root.rglob("*"):
        if f.is_file() and not any(p in _EXCLUDE for p in f.parts):
            lx = _EXT_LANG.get(f.suffix.lower(), "")
            if lx:
                counts[lx] += 1
    if not counts:
        return ""
    top = counts.most_common(1)[0][0]
    return "TypeScript" if top == "JavaScript" and counts.get("TypeScript", 0) else top


def _detect_framework(root: Path) -> str:
    cache: dict[str, str] = {}
    for cfg, fw, kw in _FW_SIG:
        if cfg not in cache:
            cache[cfg] = _rd(root / cfg).lower()
        if kw.lower() in cache[cfg]:
            return fw
    return ""


def _detect_source_dirs(root: Path) -> tuple[str, ...]:
    dirs: list[str] = []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return ("src/",)
    if any(f.is_file() and f.suffix.lower() in _CODE_EXTS for f in entries):
        dirs.append("./")
    for d in entries:
        if d.is_dir() and d.name not in _EXCLUDE and not d.name.startswith("."):
            if any(f.suffix.lower() in _CODE_EXTS for f in d.rglob("*") if f.is_file()):
                dirs.append(f"{d.name}/")
    return tuple(dirs) or ("src/",)


def _detect_test_dirs(root: Path) -> tuple[str, ...]:
    dirs = [f"{n}/" for n in ("tests", "test", "__tests__", "spec") if (root / n).is_dir()]
    skip = _EXCLUDE | {"tests", "test", "__tests__", "spec"}
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return tuple(dirs) or ("tests/",)
    for d in entries:
        if d.is_dir() and d.name not in skip and not d.name.startswith("."):
            if any(any(fnmatch.fnmatch(f.name, p) for p in _TEST_PATS)
                   for f in d.rglob("*") if f.is_file()):
                dirs.append(f"{d.name}/")
    if "./" not in dirs and any(
        any(fnmatch.fnmatch(f.name, p) for p in _TEST_PATS)
        for f in entries if f.is_file()
    ):
        dirs.append("./")
    return tuple(dirs) or ("tests/",)


def _detect_strategy(root: Path) -> tuple[str, str]:
    """Detect deployment strategy: ``console`` or ``web``."""
    has_docker = (root / "Dockerfile").is_file()
    has_compose = (root / "docker-compose.yml").is_file() or (
        root / "docker-compose.yaml").is_file()
    # Web indicators: Dockerfile, docker-compose, CI configs, web frameworks
    if has_docker or has_compose:
        return "web", "high"
    web_markers = ("cdk.json", "samconfig.toml", "buildspec.yml",
                   "azure-pipelines.yml", "cloudbuild.yaml", ".github/workflows")
    if any((root / m).exists() for m in web_markers):
        return "web", "medium"
    return "console", "high"


def analyze_project(repo: str) -> AnalysisResult:
    """Perform deterministic analysis of a project repository."""
    root = Path(repo)
    if not root.is_dir():
        return AnalysisResult(description=f"Directory not found: {repo}")
    lang, fw = _detect_language(root), _detect_framework(root)
    cmds = _CMD.get(fw) or _CMD.get(lang, ("", "", "", ()))
    build, test, run, tools = cmds
    strat, conf = _detect_strategy(root)
    has_iac = any((root / p).exists()
                  for p in ("terraform", "cdk.json", "serverless.yml", "pulumi"))
    has_db = False
    for cfg in ("package.json", "pyproject.toml", "requirements.txt",
                "Cargo.toml", "go.mod", "Gemfile", "pom.xml"):
        if any(kw in _rd(root / cfg).lower() for kw in _DB_KW):
            has_db = True
            break
    if not has_db:
        cmp = (_rd(root / "docker-compose.yml") + _rd(root / "docker-compose.yaml"))
        has_db = any(d in cmp.lower() for d in ("postgres", "mysql", "mongodb", "redis"))
    bimg = _BASE_IMG.get(lang, "debian:bookworm-slim")
    if lang == "Java" and ((root / "build.gradle").is_file()
                           or (root / "build.gradle.kts").is_file()):
        bimg = "gradle:8.5-jdk21-jammy"
    desc = (f"{lang}/{fw} project" if fw
            else (f"{lang} project" if lang else "Unknown project"))
    return AnalysisResult(
        language=lang, framework=fw, detected_strategy=strat,
        confidence=conf, description=desc,
        build_cmd=build, test_cmd=test, run_cmd=run,
        base_image=bimg, required_tools=tools,
        source_dirs=_detect_source_dirs(root),
        test_dirs=_detect_test_dirs(root),
        has_web_server=fw in _WEB_FW, has_database=has_db,
        has_iac=has_iac)


def display_analysis_results(result: AnalysisResult) -> None:
    """Format and display analysis results to the terminal."""
    w = sys.stdout.write
    w("\n  Project Analysis Results\n  -----------------------------------------\n")
    if result.description:
        w(f"  {result.description}\n\n")
    lang = (f"{result.language} / {result.framework}" if result.framework
            else (result.language or "unknown"))
    w(f"  Language:     {lang}\n")
    if result.base_image:
        w(f"  Base image:   {result.base_image}\n")
    w(f"  Strategy:     {result.detected_strategy} (confidence: {result.confidence})\n")
    chars = [c for c, v in (("web-server", result.has_web_server),
             ("database", result.has_database), ("IaC", result.has_iac)) if v]
    if chars:
        w(f"  Detected:     {' '.join(chars)}\n")
    w("\n")
    for lbl, val in (("Build", result.build_cmd),
                     ("Test", result.test_cmd), ("Run", result.run_cmd)):
        if val:
            w(f"  {lbl}:{' ' * (6 - len(lbl))}{val}\n")
    for lbl, seq in (("Tools", result.required_tools), ("Source", result.source_dirs),
                     ("Tests", result.test_dirs)):
        if seq:
            w(f"  {lbl}:{' ' * (6 - len(lbl))}{', '.join(seq)}\n")
    w("  -----------------------------------------\n")


_STRAT_MENU = (
    ("1", "console", "Console     CLI tool, no server deployment"),
    ("2", "web", "Web         Web app with Docker, CI/CD"))


def _prompt_strategy(result: AnalysisResult) -> AnalysisResult:
    w = sys.stdout.write
    w("\n  Select deployment strategy:\n\n")
    for num, _, label in _STRAT_MENU:
        w(f"    [{num}] {label}\n")
    w("\n")
    try:
        choice = input("  Choice [1]: ").strip() or "1"
    except (EOFError, KeyboardInterrupt):
        choice = "1"
    strat = next((s for n, s, _ in _STRAT_MENU if choice == n), "console")
    return replace(result, detected_strategy=strat, confidence="high")


def confirm_or_override_analysis(result: AnalysisResult) -> AnalysisResult:
    """Allow interactive override of low-confidence results."""
    if not sys.stdin.isatty():
        return result
    if result.confidence not in ("high", "medium"):
        sys.stdout.write("\n  ! Low confidence -- please select strategy.\n")
        return _prompt_strategy(result)
    sys.stdout.write(
        f"\n  [Enter] Accept detected strategy ({result.detected_strategy})\n"
        "  [o]     Override -- choose a different strategy\n\n")
    try:
        choice = input("  Choice: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return result
    if choice not in ("o", "override") and result.detected_strategy in ("console", "web"):
        return result
    return _prompt_strategy(result)
