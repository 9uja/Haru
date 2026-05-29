"""레이드 보스 콘텐츠 설정.

새 보스를 추가하거나 기존 보스의 수치를 조정하는 곳입니다.
이 파일만 수정하면 `/레이드소환` 선택지에 자동으로 반영됩니다.

══════════════════════════════════════════════════════════════════
새 보스 추가 가이드 (4단계)
══════════════════════════════════════════════════════════════════

1) 아래 BOSSES 딕셔너리에 새 키를 추가:
   BOSSES["my_boss"] = BossDef(...)

2) 필수 필드:
   key            — BOSSES 의 dict 키와 동일해야 함 (영문 snake_case 권장)
   name           — 임베드에 표시될 한국어 이름
   emoji          — 1글자 이모지 (제목/로그 등에 사용)
   level          — 표시용 레벨(밸런스에 직접 영향은 없음)
   max_hp         — 보스 최대 HP (INT)
   time_limit     — timedelta(minutes=15) 같은 형태
   base_xp        — 비례 보상 기준치 (XP)
   final_blow_xp  — 결정타 보너스 XP

3) 권장 필드:
   tier           — "일반" | "엘리트" | "레이드" | "월드"
   color          — 임베드 기본 색 (페이즈에서 색이 바뀜)
   flavor_lines   — 라이브 임베드 로그용 분위기 문구 리스트
   phases         — PhaseDef 리스트 (HP 비율 순으로)
   drop_table     — 일반 드롭 풀
   final_blow_table — 결정타 별도 풀 (희귀 가중↑)
   armor_*        — 방어력 (0=영향 없음, 100=50% 감소)
   evasion        — 회피 확률 0~1
   traits         — 특성 dict (아래 표 참고)

4) 봇 재시작하면 즉시 반영. 새 보스가 `/레이드소환` 선택지에 등장합니다.

══════════════════════════════════════════════════════════════════
데미지 타입 (스킬별 자동 분류)
══════════════════════════════════════════════════════════════════
  "물리"  ⚔️ 평타·강타·트리플 등 → armor_physical 적용
  "속성"  🔮 약점간파             → armor_elemental 적용
  "신성"  ✨ 행운의일격           → armor_holy 적용

══════════════════════════════════════════════════════════════════
방어 공식
══════════════════════════════════════════════════════════════════
  final = raw × (1 − armor / (armor + 100))

  armor 0   → 영향 없음
  armor 20  → 17% 감소
  armor 50  → 33% 감소
  armor 100 → 50% 감소
  armor 200 → 67% 감소

══════════════════════════════════════════════════════════════════
특성(trait) 카탈로그
══════════════════════════════════════════════════════════════════
  "crit_resist"       {}                                — 치명타 발동 무효
  "weakness_resist"   {}                                — 약점 공격 무효
  "damage_cap"        {"value": 400}                    — 단일 데미지 상한
  "regen"             {"per_min": 80}                   — 분당 HP 회복
  "armor_break"       {"at_pct": 0.25, "mult": 0.5}     — HP n%↓ 시 방어 ×mult
  "phase_heal"        {"pct": 0.10}                     — 페이즈 전환 시 HP +n%

══════════════════════════════════════════════════════════════════
드롭 아이템 키 (cogs/leveling.py 의 ITEMS 와 일치해야 함)
══════════════════════════════════════════════════════════════════
  "ration_small"  🍙 작은 비상식량 (1.5x / 30분)
  "ration_large"  🍱 큰 비상식량   (2x / 20분)
  "elixir"        🧪 엘릭서        (3x / 10분)
  "lucky_charm"   🍀 행운의 부적   (5x / 5분, 희귀)

══════════════════════════════════════════════════════════════════
보스 이미지 (둘 다 선택, 둘 다 같이 써도 됨)
══════════════════════════════════════════════════════════════════
A) 외부 URL 방식 (가장 간단)
   1. 이미지를 어디든 호스팅 (Discord 채널에 업로드 추천)
   2. Discord 에서 이미지 우클릭 → "링크 복사"
   3. raid_config.py 에 붙여넣기:
        image_url="https://cdn.discordapp.com/attachments/.../boss.png",
        thumbnail_url="https://cdn.discordapp.com/attachments/.../boss_icon.png",

B) 로컬 파일 방식 (자동 업로드)
   1. assets/raid/ 폴더에 파일 두기 (예: fire_golem.png)
   2. raid_config.py 에 파일명만 지정:
        image_file="fire_golem.png",
        thumbnail_file="fire_golem_icon.png",
   3. 첫 /레이드소환 시 봇이 자동 업로드 → CDN URL 캐시 → 이후 재사용

표시 위치:
  image_*      → 임베드 하단의 큰 이미지 (set_image)
  thumbnail_*  → 임베드 우상단 작은 아이콘 (set_thumbnail)

권장 해상도:
  image      640×360 이상 (16:9 비율 권장)
  thumbnail  256×256 (정사각 권장)
"""
from __future__ import annotations

from datetime import timedelta

import discord

from raid_core import BossDef, PhaseDef, DropEntry


BOSSES: dict[str, BossDef] = {
    # ────────────────────────────────────────────────────────
    # 🔥 일반: 화염 골렘 (Lv 25)
    #    물리 보통 / 속성 강함 / 신성 무방비 / 회피 0 / 특성 없음
    # ────────────────────────────────────────────────────────
    "fire_golem": BossDef(
        key="fire_golem",
        name="화염 골렘",
        emoji="🔥",
        level=25,
        tier="일반",
        max_hp=2_500,
        time_limit=timedelta(minutes=15),
        base_xp=200,
        final_blow_xp=500,
        flavor_lines=[
            "화염 골렘이 포효한다 🔥",
            "용암을 분출한다 💥",
            "땅을 울리며 발을 구른다 🌋",
            "화염 갑옷이 빛난다 ✨",
            "분노로 눈이 붉게 타오른다 👹",
        ],
        color=discord.Color.dark_red(),
        phases=[
            PhaseDef(hp_pct=0.60, name="격노",
                     color=discord.Color.orange(), damage_taken_mult=1.2,
                     flavor="🔥 **화염 골렘이 격노한다!** 받는 데미지 +20%"),
            PhaseDef(hp_pct=0.30, name="파괴",
                     color=discord.Color.red(), damage_taken_mult=1.5,
                     flavor="💥 **화염 골렘의 파괴 형태!** 약점 노출 — 받는 데미지 +50%"),
        ],
        drop_table=[
            DropEntry(50, "ration_small"),
            DropEntry(25, "ration_large"),
            DropEntry(10, "elixir"),
            DropEntry(3,  "lucky_charm"),
        ],
        final_blow_table=[
            DropEntry(20, "ration_small"),
            DropEntry(30, "ration_large"),
            DropEntry(30, "elixir"),
            DropEntry(15, "lucky_charm"),
        ],
        armor_physical=20,
        armor_elemental=60,
        armor_holy=0,
        evasion=0.0,
        traits={},
    ),

    # ────────────────────────────────────────────────────────
    # ❄️ 엘리트: 얼음 늑대 (Lv 45)
    #    물리 강함 / 속성 약함(INT 빌드 유리) / 회피 15% / 치명저항 + 재생
    # ────────────────────────────────────────────────────────
    "ice_wolf": BossDef(
        key="ice_wolf",
        name="얼음 늑대",
        emoji="❄️",
        level=45,
        tier="엘리트",
        max_hp=8_000,
        time_limit=timedelta(minutes=30),
        base_xp=500,
        final_blow_xp=1500,
        flavor_lines=[
            "얼음 늑대가 으르렁댄다 ❄️",
            "냉기를 내뿜는다 🌬️",
            "흰 털이 곤두선다 🐺",
            "송곳니가 번뜩인다 🦷",
            "발자국마다 얼음이 솟는다 🧊",
        ],
        color=discord.Color.blue(),
        phases=[
            PhaseDef(hp_pct=0.50, name="격노",
                     color=discord.Color.dark_blue(), damage_taken_mult=1.3,
                     flavor="🐺 **얼음 늑대가 광기에 휩싸인다!** 받는 데미지 +30%"),
            PhaseDef(hp_pct=0.20, name="빙결 폭주",
                     color=discord.Color.from_rgb(180, 240, 255),
                     damage_taken_mult=1.8,
                     flavor="❄️ **빙결 폭주!** 늑대의 가죽이 깨진다 — 받는 데미지 +80%"),
        ],
        drop_table=[
            DropEntry(30, "ration_small"),
            DropEntry(30, "ration_large"),
            DropEntry(20, "elixir"),
            DropEntry(8,  "lucky_charm"),
        ],
        final_blow_table=[
            DropEntry(15, "ration_small"),
            DropEntry(25, "ration_large"),
            DropEntry(35, "elixir"),
            DropEntry(25, "lucky_charm"),
        ],
        armor_physical=40,
        armor_elemental=25,
        armor_holy=35,
        evasion=0.15,
        traits={
            "crit_resist": {},
            "regen": {"per_min": 80},
        },
    ),

    # ────────────────────────────────────────────────────────
    # 🌑 레이드: 그림자 기사 (Lv 75)
    #    중장갑 / 신성 약점 / 강철가죽 + 페이즈회생 + 갑주균열
    # ────────────────────────────────────────────────────────
    "shadow_knight": BossDef(
        key="shadow_knight",
        name="그림자 기사",
        emoji="🌑",
        level=75,
        tier="레이드",
        max_hp=20_000,
        time_limit=timedelta(minutes=60),
        base_xp=1_000,
        final_blow_xp=3000,
        flavor_lines=[
            "그림자 기사가 검을 빼든다 ⚔️",
            "어둠이 몰려든다 🌑",
            "검은 망토가 펄럭인다 🦇",
            "어둠의 칼날이 빛난다 🗡️",
            "전장의 빛이 사라진다 🕯️",
        ],
        color=discord.Color.dark_purple(),
        phases=[
            PhaseDef(hp_pct=0.70, name="분노",
                     color=discord.Color.purple(), damage_taken_mult=1.15,
                     flavor="🌑 **그림자 기사의 분노!** 받는 데미지 +15%"),
            PhaseDef(hp_pct=0.40, name="광기",
                     color=discord.Color.magenta(), damage_taken_mult=1.3,
                     flavor="🗡️ **광기의 형태!** 검광이 흩어진다 — 받는 데미지 +30%"),
            PhaseDef(hp_pct=0.15, name="멸세",
                     color=discord.Color.red(), damage_taken_mult=1.6,
                     flavor="💀 **멸세의 일격!** 갑주가 무너졌다 — 받는 데미지 +60%"),
        ],
        drop_table=[
            DropEntry(15, "ration_small"),
            DropEntry(25, "ration_large"),
            DropEntry(30, "elixir"),
            DropEntry(20, "lucky_charm"),
        ],
        final_blow_table=[
            DropEntry(10, "ration_small"),
            DropEntry(20, "ration_large"),
            DropEntry(35, "elixir"),
            DropEntry(35, "lucky_charm"),
        ],
        armor_physical=70,
        armor_elemental=45,
        armor_holy=20,
        evasion=0.08,
        traits={
            "damage_cap": {"value": 400},
            "phase_heal": {"pct": 0.10},
            "armor_break": {"at_pct": 0.25, "mult": 0.5},
        },
    ),

    # ────────────────────────────────────────────────────────
    # 여기에 새 보스를 추가하세요!
    #
    # 예시 템플릿:
    # "boss_key": BossDef(
    #     key="boss_key", name="보스 이름", emoji="🦴",
    #     level=60, tier="엘리트",
    #     max_hp=12_000, time_limit=timedelta(minutes=30),
    #     base_xp=700, final_blow_xp=2000,
    #     flavor_lines=["...", "..."],
    #     color=discord.Color.from_rgb(150, 80, 200),
    #     phases=[
    #         PhaseDef(hp_pct=0.50, name="...",
    #                  color=discord.Color.purple(),
    #                  damage_taken_mult=1.3,
    #                  flavor="..."),
    #     ],
    #     drop_table=[DropEntry(30, "ration_large"), DropEntry(10, "elixir")],
    #     final_blow_table=[DropEntry(20, "lucky_charm")],
    #     armor_physical=50, armor_elemental=30, armor_holy=40,
    #     evasion=0.10,
    #     traits={"regen": {"per_min": 60}},
    #     # 이미지 (둘 중 한쪽 또는 둘 다 사용 가능)
    #     image_url="https://cdn.discordapp.com/attachments/.../boss.png",  # 외부 URL
    #     thumbnail_url="https://cdn.discordapp.com/attachments/.../icon.png",
    #     # 또는 로컬 파일 (assets/raid/ 안에 두면 봇이 첫 소환에 자동 업로드)
    #     image_file="my_boss.png",
    #     thumbnail_file="my_boss_icon.png",
    # ),
    # ────────────────────────────────────────────────────────
}
