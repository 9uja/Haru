"""봇 최고 등급 관리자(오너) 정책.

이 ID 한 명만이 봇의 모든 명령을 **최우선 권한**으로 실행할 수 있다.
- 슬래시 명령 권한 게이트(`/관리 …`)
- AI 자연어 명령(예: `하루야 DB 초기화 chat`, `하루야 <@123> 무시`)
- 유저별 무시/지시 같은 봇 정책 변경

서버 권한(관리자 역할 등)이 아니라 **고정 디스코드 ID**로만 식별한다.
ID 변경이 필요하면 이 상수를 수정하거나, 향후 환경변수화 한다.
"""
from __future__ import annotations

OWNER_ID: int = 379934490866352130
OWNER_NICKNAME: str = "구자"


def is_owner(user_id: int) -> bool:
    """주어진 디스코드 유저 ID가 봇 오너인지."""
    return user_id == OWNER_ID
