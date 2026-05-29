"""스킬 콘텐츠 + 해방 조건 (비밀 가챠).

각 스킬의 마나·쿨다운·데미지·해방 조건을 여기서만 조정합니다.
새 스킬을 "추가" 하려면 코그 측 버튼/dispatch 도 같이 손봐야 하지만,
기존 스킬의 수치 조정과 **해방 조건 변경** 은 이 파일만 수정하면 됩니다.

══════════════════════════════════════════════════════════════════
해방 조건 (SkillRequirement)
══════════════════════════════════════════════════════════════════
조건은 모두 AND. 비어있는 필드(0)는 무시.

  min_level     — 누적 레벨 이상이어야 함
  min_str       — STR 분배 포인트 이상
  min_agi       — AGI 분배 포인트 이상
  min_int       — INT 분배 포인트 이상
  min_luk       — LUK 분배 포인트 이상
  min_total     — STR+AGI+INT+LUK 합 이상

══════════════════════════════════════════════════════════════════
조건 비밀 정책
══════════════════════════════════════════════════════════════════
- **미해방 스킬은 패널에 버튼이 표시되지 않음** (존재 자체가 비밀)
- 조건은 어떤 슬래시·임베드에서도 노출되지 않음
- **해방 시점에만 DM** 으로 "🎉 새 스킬 해방: 강타!" + 상세 안내
- 만약 DM 차단되어 있으면 조용히 실패 (`/레이드참가` 시 자동으로 버튼이 등장)
- 해방 체크 트리거: 레벨업 / 스탯 분배 / `/레이드참가` 직전

══════════════════════════════════════════════════════════════════
조건 설계 가이드 (밸런스 의도)
══════════════════════════════════════════════════════════════════
- 입문(평타만, 무제한)             : 누구나 즉시 사용
- 첫 스킬(5~10레벨)                : 1차 도전 동기
- 중간 스킬(15~20레벨, 특정 스탯) : 빌드 차별화 시작
- 후반 스킬(25레벨+, 깊은 스탯)   : 빌드 전문화 보상

조건을 너무 낮게 두면 도파민이 약하고, 너무 높으면 좌절합니다.

══════════════════════════════════════════════════════════════════
데미지 공식 (DamageFormula) — 스킬별 자유 조정
══════════════════════════════════════════════════════════════════
스킬 1회 raw 데미지:
   raw = base_damage × (1 + STR×str_scale + AGI×agi_scale
                         + INT×int_scale + LUK×luk_scale)

치명 확률 = min(crit_cap, crit_base + LUK×luk_crit_coef)
치명 배수 = crit_base_mult + AGI×agi_crit_coef
약점 확률 = min(weakness_cap, weakness_base + INT×int_weak_coef)
약점 배수 = weakness_mult

이후 final = raw × multiplier × phase_mult.
모든 필드는 생략 가능(기본값 사용). 생략 시 평타 표준값을 따른다.

빌드 친화도 설계 예시:
- str_scale 높임  → STR 빌드 친화 (강타 등)
- agi_scale 높임  → AGI 빌드 친화 (화살비 등)
- int_scale 높임  → INT 빌드 친화 (약점간파 등)
- luk_scale 높임  → LUK 빌드 친화 (행운의일격 등)

평타 기본 공식은 아래 ATK_FORMULA 로 별도 노출 — 평타도 조정 가능.
"""
from __future__ import annotations

from datetime import timedelta

from raid_core import (
    SkillDef, SkillRequirement, DamageFormula,
    DMG_PHYS, DMG_ELEM, DMG_HOLY,
)


# ─── 평타 데미지 공식 (모든 유저 공통, 조건 없음) ───
ATK_FORMULA = DamageFormula(
    base_damage=10.0,
    str_scale=0.02,       # 평타는 STR 만 스케일 (기본형)
)


SKILLS: dict[str, SkillDef] = {
    # ─── 강타 (STR 빌드) ─ STR 가산 가중치 ↑ ──────────────────
    "slash": SkillDef(
        key="slash", name="강타", emoji="💥",
        mana_cost=30, cooldown=timedelta(seconds=60),
        description="강력한 일격",
        multiplier=1.8, damage_type=DMG_PHYS,
        requirements=SkillRequirement(min_level=5, min_str=10),
        formula=DamageFormula(
            base_damage=10.0,
            str_scale=0.025,   # 평타 0.02 보다 ↑ — STR 빌드 보상
        ),
    ),

    # ─── 화살비 (AGI 빌드) ─ STR/AGI 혼합 스케일 ───────────────
    "triple": SkillDef(
        key="triple", name="트리플 어택", emoji="3️⃣",
        mana_cost=50, cooldown=timedelta(seconds=90),
        description="3연속 공격",
        multiplier=0.6, hits=3, damage_type=DMG_PHYS,
        requirements=SkillRequirement(min_level=10, min_agi=25),
        formula=DamageFormula(
            base_damage=10.0,
            str_scale=0.012,
            agi_scale=0.012,   # AGI 도 함께 스케일 (히트가 많아 가산↑↑)
            # AGI 추가 보너스: 치명타 발동률 가산
            crit_base=0.10,
            luk_crit_coef=0.002,
        ),
    ),

    # ─── 약점간파 (INT 빌드) ─ INT 가 주력 스케일 ──────────────
    "pierce": SkillDef(
        key="pierce", name="약점간파", emoji="🎯",
        mana_cost=60, cooldown=timedelta(seconds=120),
        description="약점 적중 확정",
        multiplier=2.5, force_weakness=True, damage_type=DMG_ELEM,
        requirements=SkillRequirement(min_level=15, min_int=40),
        formula=DamageFormula(
            base_damage=8.0,    # 기본은 약간 낮춤
            str_scale=0.005,    # STR 영향 최소화
            int_scale=0.035,    # INT 가 메인 (다른 스킬보다 ↑↑)
            weakness_mult=2.5,  # 약점 적중 시 데미지 더 강함
        ),
    ),

    # ─── 행운의 일격 (LUK 빌드) ─ LUK 가 주력 스케일 + 치명 ───
    "fortune": SkillDef(
        key="fortune", name="행운의 일격", emoji="✨",
        mana_cost=40, cooldown=timedelta(seconds=100),
        description="치명타 확정",
        multiplier=2.0, force_crit=True, damage_type=DMG_HOLY,
        requirements=SkillRequirement(min_level=20, min_luk=50),
        formula=DamageFormula(
            base_damage=10.0,
            str_scale=0.005,
            luk_scale=0.03,     # LUK 가 메인 스케일
            crit_base_mult=2.0, # 치명 발동 시 기본 배수 ↑ (1.5 → 2.0)
            agi_crit_coef=0.008,# AGI 가 치명 배수 가산 ↑
        ),
    ),
}


# UI 가 참조하는 액션 순서 (평타 + 스킬 4종)
ACTION_KEYS: list[str] = ["atk"] + list(SKILLS.keys())
