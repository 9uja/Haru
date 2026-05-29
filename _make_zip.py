"""배포용 ZIP 생성기 (1회용 보조 스크립트).

비밀·캐시·VCS·가상환경을 엄격히 제외하고 봇 소스만 묶는다.
"""
from __future__ import annotations

import os
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 포함할 최상위 항목(파일/디렉토리)
INCLUDE = [
    "bot.py", "config.py", "database.py", "owner.py", "http_guard.py",
    "keepalive.py", "views.py", "requirements.txt", "Dockerfile",
    "start.sh", ".dockerignore", ".env.example", "README.md",
    "raid_core.py", "raid_config.py", "skill_config.py",
    "cogs", "deploy", "docs", "assets", "scripts",
]

# 어떤 경로 부분 문자열이라도 매치되면 제외
EXCLUDE_PATTERNS = [
    re.compile(r"(?:^|[\\/])\.env$"),                # .env (어디든)
    re.compile(r"(?:^|[\\/])\.env\.bak$"),
    re.compile(r"(?:^|[\\/])\.env\.local$"),
    re.compile(r"(?:^|[\\/])SECURITY\.md$"),         # docs/SECURITY.md 등
    re.compile(r"(?:^|[\\/])__pycache__(?:[\\/]|$)"),
    re.compile(r"\.pyc$"),
    re.compile(r"\.pyo$"),
    re.compile(r"(?:^|[\\/])\.git(?:[\\/]|$)"),
    re.compile(r"(?:^|[\\/])\.venv(?:[\\/]|$)"),
    re.compile(r"(?:^|[\\/])\.idea(?:[\\/]|$)"),
    re.compile(r"(?:^|[\\/])\.vscode(?:[\\/]|$)"),
    re.compile(r"(?:^|[\\/])\.claude(?:[\\/]|$)"),
    re.compile(r"(?:^|[\\/])\.DS_Store$"),
    re.compile(r"(?:^|[\\/])Thumbs\.db$"),
    re.compile(r"(?:^|[\\/])secrets\."),
    re.compile(r"\.secret$"),
]


def is_excluded(rel_path: str) -> bool:
    """ZIP 내부 경로(rel_path)가 제외 대상이면 True."""
    return any(p.search(rel_path) for p in EXCLUDE_PATTERNS)


def walk_files(item: str):
    """include 항목 하나를 풀어 (절대경로, ZIP 내부 경로) 튜플 시퀀스로 반환."""
    src = ROOT / item
    if not src.exists():
        print(f"WARN missing: {item}")
        return
    if src.is_file():
        yield src, item.replace(os.sep, "/")
        return
    for dirpath, dirnames, filenames in os.walk(src):
        # 디렉토리 단계에서 제외 디렉토리 가지치기
        dirnames[:] = [
            d for d in dirnames
            if not is_excluded(os.path.relpath(os.path.join(dirpath, d), ROOT))
        ]
        for fn in filenames:
            full = Path(dirpath) / fn
            rel = full.relative_to(ROOT).as_posix()
            if is_excluded(rel):
                continue
            yield full, rel


def main() -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    zip_path = ROOT / f"HaruBot-deploy-{stamp}.zip"
    if zip_path.exists():
        zip_path.unlink()

    added: list[tuple[str, int]] = []
    skipped: list[str] = []

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for item in INCLUDE:
            for full, rel in walk_files(item):
                zf.write(full, arcname=rel)
                added.append((rel, full.stat().st_size))

        # 최종 안전 검사: ZIP에 누출 파일이 들어가지 않았는지 확인
        for name in zf.namelist():
            if is_excluded(name):
                skipped.append(name)

    if skipped:
        print("❌ LEAK DETECTED — ZIP에 다음 항목이 포함됨:")
        for s in skipped:
            print(f"   {s}")
        zip_path.unlink(missing_ok=True)
        return 1

    print(f"OK: {zip_path.name}")
    print(f"Size: {zip_path.stat().st_size / 1024:.1f} KB")
    print(f"파일 {len(added)}개:")
    for rel, sz in sorted(added):
        print(f"  {sz:>9,d}  {rel}")

    # 최종 시큐리티 더블체크: 비밀번호 같은 문자열이 들어갔는지 빠른 스캔
    # (Windows cp949 콘솔 호환을 위해 ASCII 마커만 사용)
    print("\n=== 비밀 마커 스캔 (.env 토큰/DSN 형식) ===")
    leak_found = False
    placeholder_passwords = {
        "password", "PASSWORD", "<password>", "your_password", "your-password",
        "user:password", "USER:PASSWORD",
        "pass", "PASS", "<pass>", "secret", "SECRET", "yourpassword", "YOURPASSWORD",
    }
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if name.endswith("/") or name.lower().endswith((".png", ".jpg", ".jpeg", ".ico")):
                continue
            try:
                data = zf.read(name).decode("utf-8", errors="ignore")
            except Exception:
                continue
            # 디스코드 봇 토큰: 'M' 시작 + 영문/숫자/._- 24자 이상
            if re.search(r"DISCORD_TOKEN\s*=\s*[MNO][A-Za-z0-9._-]{30,}", data):
                print(f"  [WARN] 잠재 토큰: {name}")
                leak_found = True
            # postgres DSN: 비밀번호 자리가 자리표시자가 아닐 때만 leak
            for m in re.finditer(r"postgresql://([^:\s]+):([^@\s]+)@", data):
                if m.group(2) not in placeholder_passwords:
                    print(f"  [WARN] 잠재 DSN: {name}  (user={m.group(1)})")
                    leak_found = True
    print("  [OK] 비밀 마커 없음" if not leak_found else "  [FAIL] 검토 필요")
    return 1 if leak_found else 0


if __name__ == "__main__":
    sys.exit(main())
