"""레이드 시스템 공용 타입·상수.

보스 콘텐츠(`raid_config.py`) 와 게임 로직(`cogs/raid.py`) 가 공유하는 정의만 둔다.
순환 임포트를 피하기 위해 이 모듈은 어떤 cogs.* 도 임포트하지 않는다.

여기에 정의된 것:
- 데미지 타입 상수 (DMG_PHYS / DMG_ELEM / DMG_HOLY)
- 데이터 클래스: PhaseDef, DropEntry, TraitDef, BossDef
- 특성 카탈로그(TRAITS) — 이름/이모지/설명만(동작 분기는 cogs/raid.py)
- 가중 추첨 유틸: roll_drop()
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

import discord


# ───────── 데미지 타입 ─────────
DMG_PHYS = "물리"   # ⚔️ 평타·강타·트리플(화살비) 등 (STR/AGI)
DMG_ELEM = "속성"   # 🔮 약점간파 (INT)
DMG_HOLY = "신성"   # ✨ 행운의일격 (LUK)
DMG_TYPE_EMOJI = {DMG_PHYS: "⚔️", DMG_ELEM: "🔮", DMG_HOLY: "✨"}


# ───────── 페이즈 정의 ─────────
@dataclass
class PhaseDef:
    """HP 비율 이하 도달 시 진입하는 페이즈.
    damage_taken_mult: 이 페이즈에서 보스가 받는 데미지 가중치(1.0 = 효과 없음).
    """
    hp_pct: float
    name: str
    color: discord.Color
    damage_taken_mult: float = 1.0
    flavor: str = ""


# ───────── 드롭 테이블 정의 ─────────
@dataclass
class DropEntry:
    """가중치 기반 드롭. weight 가 클수록 자주 등장."""
    weight: int
    item_key: str   # cogs.leveling.ITEMS 의 key


# ───────── 특성 정의 ─────────
@dataclass
class TraitDef:
    """보스 패시브 효과 정의(메타). 동작 자체는 cogs/raid.py 에서 분기.

    동작 정의 파라미터는 보스의 traits dict 에 함께 보관:
      damage_cap   : {"value": 400}
      regen        : {"per_min": 100}
      phase_heal   : {"pct": 0.15}
      armor_break  : {"at_pct": 0.30, "mult": 0.5}
      crit_resist  : {}
      weakness_resist : {}
    """
    key: str
    name: str
    emoji: str
    description: str


TRAITS: dict[str, TraitDef] = {
    "crit_resist": TraitDef("crit_resist", "치명 저항", "🛡️",
        "치명타 발동을 무효화"),
    "weakness_resist": TraitDef("weakness_resist", "약점 봉인", "🕊️",
        "약점 공격 발동을 무효화"),
    "damage_cap": TraitDef("damage_cap", "강철 가죽", "🪨",
        "단일 공격 데미지에 상한이 있다"),
    "regen": TraitDef("regen", "재생", "💚",
        "시간이 흐르면 HP를 회복한다"),
    "armor_break": TraitDef("armor_break", "갑주 균열", "⚒️",
        "HP가 낮아지면 방어력이 줄어든다"),
    "phase_heal": TraitDef("phase_heal", "페이즈 회생", "🩸",
        "페이즈 전환 시 HP를 일부 회복한다"),
}


# ───────── 보스 정의 ─────────
@dataclass
class BossDef:
    """보스 1마리의 모든 데이터.

    필수: key, name, emoji, level, max_hp, time_limit, base_xp, final_blow_xp
    선택: 페이즈/드롭/방어/회피/특성
    """
    key: str
    name: str
    emoji: str
    level: int
    max_hp: int
    time_limit: timedelta
    base_xp: int
    final_blow_xp: int
    flavor_lines: list[str] = field(default_factory=list)
    color: discord.Color = discord.Color.dark_red()
    phases: list[PhaseDef] = field(default_factory=list)
    drop_table: list[DropEntry] = field(default_factory=list)
    final_blow_table: list[DropEntry] = field(default_factory=list)
    tier: str = "일반"
    # 방어/회피/특성
    armor_physical: int = 0    # damage / (armor+100) 비례 감소
    armor_elemental: int = 0
    armor_holy: int = 0
    evasion: float = 0.0        # 0~1, 보스 회피 확률
    traits: dict[str, dict] = field(default_factory=dict)
    # ─── 이미지 (URL 또는 로컬 파일, 둘 다 가능) ───
    image_url: str = ""         # 외부 URL (예: Discord CDN, Imgur). 큰 이미지로 표시.
    image_file: str = ""        # 로컬 파일명 (assets/raid/ 기준). 봇이 자동 업로드.
    thumbnail_url: str = ""     # 외부 썸네일 URL. 코너 작은 이미지.
    thumbnail_file: str = ""    # 로컬 썸네일 파일명. 봇이 자동 업로드.


# ───────── 데미지 공식 ─────────
@dataclass
class DamageFormula:
    """스킬·평타의 raw 데미지 공식 파라미터. 모든 필드는 기본값을 가진다.

    1차 스탯 스케일 (raw_damage):
       raw = base_damage * (1 + STR*str_scale + AGI*agi_scale
                              + INT*int_scale + LUK*luk_scale)

    치명 (raw 에 곱연산):
       crit_chance = min(crit_cap, crit_base + LUK*luk_crit_coef)
       crit_mult   = crit_base_mult + AGI*agi_crit_coef

    약점 (raw 에 추가 곱연산):
       weak_chance = min(weakness_cap, weakness_base + INT*int_weak_coef)
       weak_mult   = weakness_mult

    이후 호출 측에서 final = raw * (skill.multiplier) * (phase_mult) 적용.
    """
    base_damage: float = 10.0
    # 스탯 스케일 — 스킬별 빌드 친화도를 결정
    str_scale: float = 0.02
    agi_scale: float = 0.0
    int_scale: float = 0.0
    luk_scale: float = 0.0
    # 치명
    crit_base: float = 0.05
    crit_cap: float = 0.50
    luk_crit_coef: float = 0.003
    crit_base_mult: float = 1.5
    agi_crit_coef: float = 0.005
    # 약점
    weakness_base: float = 0.0
    weakness_cap: float = 0.40
    int_weak_coef: float = 0.002
    weakness_mult: float = 2.0


# ───────── 스킬 정의 + 해방 조건 ─────────
@dataclass
class SkillRequirement:
    """스킬 해방 조건. 0/빈 필드는 무시. 모두 AND 조합.

    조건이 채워지면 유저가 자동 해방 + DM 알림 받음. 조건 자체는 비밀.
    """
    min_level: int = 0
    min_str: int = 0
    min_agi: int = 0
    min_int: int = 0
    min_luk: int = 0
    min_total: int = 0   # str+agi+int+luk 합


@dataclass
class SkillDef:
    """스킬 1종 정의. 동작 파라미터 + 해방 조건 + UI 텍스트 + 데미지 공식."""
    key: str
    name: str
    emoji: str
    mana_cost: int
    cooldown: timedelta
    description: str
    multiplier: float = 1.0
    hits: int = 1
    force_crit: bool = False
    force_weakness: bool = False
    damage_type: str = DMG_PHYS
    requirements: SkillRequirement = field(default_factory=SkillRequirement)
    formula: DamageFormula = field(default_factory=DamageFormula)


def check_requirement(req: SkillRequirement, level: int, stats: dict) -> bool:
    """현재 레벨/스탯이 조건을 충족하는지. stats 키: str_pt/agi_pt/int_pt/luk_pt."""
    if level < req.min_level:
        return False
    if int(stats.get("str_pt", 0)) < req.min_str:
        return False
    if int(stats.get("agi_pt", 0)) < req.min_agi:
        return False
    if int(stats.get("int_pt", 0)) < req.min_int:
        return False
    if int(stats.get("luk_pt", 0)) < req.min_luk:
        return False
    total = sum(int(stats.get(k, 0)) for k in ("str_pt", "agi_pt", "int_pt", "luk_pt"))
    if total < req.min_total:
        return False
    return True


# ───────── 유틸 ─────────
def roll_drop(table: list[DropEntry]) -> Optional[str]:
    """가중치 기반 1개 추첨. 테이블 빈 경우 None."""
    if not table:
        return None
    total = sum(max(0, e.weight) for e in table)
    if total <= 0:
        return None
    r = random.uniform(0, total)
    acc = 0
    for e in table:
        acc += max(0, e.weight)
        if r <= acc:
            return e.item_key
    return table[-1].item_key
