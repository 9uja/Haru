"""보스 레이드 Phase 1 (MVP).

기획서: docs/RAID_PLAN.md (v0.2)
이번 단계 범위:
- 보스 1종(`fire_golem`)
- `/레이드소환` (관리자) — 즉시 등장
- `/레이드참가` — 개인 ephemeral 패널(본인 상태창 + 평타 버튼)
- `/레이드채널설정` (관리자)
- 라이브 임베드 — 2초 디바운스 + 3초 하드 캡 + 1015 가드
- 단일 평타만 (스킬·마나는 Phase 2)
- 종료 시 비례 XP 보상 + 결정타 보너스

D2 확정: 유저 페널티 0 (HP X, XP 차감 X). 보스 공격은 연출만(이번 단계는 생략).
D13 확정: AFK 시스템 없음. 데미지 0 = 보상 0 으로 자연 차단.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from http_guard import GUARD
from owner import is_owner
from cogs.leveling import level_from_xp, MAX_LEVEL

# 공용 타입·상수 (raid_core) + 보스 콘텐츠 (raid_config) 분리:
# - 새 보스 추가는 raid_config.py 에서만 진행 (cogs/raid.py 수정 불필요)
from raid_core import (
    DMG_PHYS, DMG_ELEM, DMG_HOLY, DMG_TYPE_EMOJI,
    PhaseDef, DropEntry, TraitDef, BossDef, TRAITS,
    SkillDef, SkillRequirement, check_requirement,
    DamageFormula,
    roll_drop,
)
from raid_config import BOSSES
from skill_config import SKILLS, ACTION_KEYS, ATK_FORMULA

log = logging.getLogger(__name__)

SILENT = discord.AllowedMentions.none()
MENTION_USER = discord.AllowedMentions(users=True, roles=False, everyone=False)

# ───────── 임베드/패널 갱신 정책 ─────────
EMBED_DEBOUNCE = 2.0    # 마지막 행동 후 2초 대기 후 edit (D11)
EMBED_HARD_CAP = 3.0    # 직전 edit 으로부터 최소 3초 간격 보장
IDLE_REFRESH = 30.0     # 액션 없는 동안의 idle 갱신 주기(시간 카운트가 계속 흐르게)
EMBED_LOG_LINES = 5     # "최근 5턴" 로그 줄 수
TIMEOUT_CHECK = 10      # 시간 초과 확인 주기(초)
PANEL_TIMEOUT = 60 * 14  # 14분 (Discord ephemeral 15분 한계 직전)

# ───────── 이미지 정책 ─────────
import os
ASSETS_RAID_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "raid")


def _resolve_asset_path(filename: str) -> Optional[str]:
    """assets/raid/ 안에서 파일 경로를 안전하게 해결. 디렉토리 탈출 차단."""
    if not filename:
        return None
    # 경로 분리 시도/절대경로/.. 금지
    if os.path.isabs(filename) or ".." in filename.replace("\\", "/").split("/"):
        log.warning("거부된 이미지 경로: %r", filename)
        return None
    full = os.path.normpath(os.path.join(ASSETS_RAID_DIR, filename))
    if not full.startswith(ASSETS_RAID_DIR):
        log.warning("경로 탈출 차단: %r", filename)
        return None
    return full if os.path.isfile(full) else None


# ───────── 전용 채널 정책 ─────────
RAID_CHANNEL_NAME = "보스레이드"
RAID_CHANNEL_TOPIC = (
    "보스 레이드 전용 채널 — `/레이드참가` 로 본인 패널 호출 / "
    "관리자: `/레이드소환` 으로 시작"
)


# (PhaseDef 는 raid_core.py 로 분리됨)


# (데미지 타입·TraitDef·TRAITS·DropEntry 정의는 raid_core.py 로 분리됨 — 위 임포트 참조)


# (BossDef 는 raid_core.py 로 분리됨)
# (BOSSES dict 는 raid_config.py 로 분리됨 — 위 임포트 참조)
# (BOSSES + _roll_drop 정의는 raid_core/raid_config 로 이전됨)



# (SkillDef·SKILLS·ACTION_KEYS 는 raid_core / skill_config 로 분리됨)


# ───────── 데미지 계산 ─────────
def _user_stats(stats_row: Optional[dict]) -> tuple[int, int, int, int]:
    """(str, agi, int, luk) 4스탯 추출. row 없으면 0."""
    if stats_row is None:
        return 0, 0, 0, 0
    return (
        int(stats_row.get("str_pt", 0) or 0),
        int(stats_row.get("agi_pt", 0) or 0),
        int(stats_row.get("int_pt", 0) or 0),
        int(stats_row.get("luk_pt", 0) or 0),
    )


_DEFAULT_FORMULA: Optional["DamageFormula"] = None  # 지연 초기화


def calc_attack(
    stats_row: Optional[dict],
    *,
    formula: Optional["DamageFormula"] = None,
    multiplier: float = 1.0,
    force_crit: bool = False,
    force_weakness: bool = False,
    phase_mult: float = 1.0,
    boss_traits: Optional[dict] = None,
) -> dict:
    """공격 1회 raw 데미지 계산(방어 적용 전).

    formula: DamageFormula. None 이면 모듈 기본값(평타 표준) 사용.
    boss_traits: 보스 특성 dict — `crit_resist`/`weakness_resist` 검사.
    """
    if boss_traits is None:
        boss_traits = {}
    global _DEFAULT_FORMULA
    if formula is None:
        if _DEFAULT_FORMULA is None:
            _DEFAULT_FORMULA = DamageFormula()
        formula = _DEFAULT_FORMULA
    s, a, i_, l = _user_stats(stats_row)
    # raw 데미지: 4스탯 스케일 가산
    damage = formula.base_damage * (
        1.0
        + s * formula.str_scale
        + a * formula.agi_scale
        + i_ * formula.int_scale
        + l * formula.luk_scale
    )
    # 치명
    if "crit_resist" in boss_traits:
        crit = False
    else:
        crit_chance = min(formula.crit_cap, formula.crit_base + l * formula.luk_crit_coef)
        crit = force_crit or random.random() < crit_chance
    if crit:
        damage *= formula.crit_base_mult + a * formula.agi_crit_coef
    # 약점
    if "weakness_resist" in boss_traits:
        weakness = False
    else:
        weak_chance = min(formula.weakness_cap, formula.weakness_base + i_ * formula.int_weak_coef)
        weakness = force_weakness or random.random() < weak_chance
    if weakness:
        damage *= formula.weakness_mult
    damage *= multiplier * phase_mult
    return {"damage": max(1, int(round(damage))), "crit": crit, "weakness": weakness}


def apply_defense(
    raw_damage: int,
    boss: BossDef,
    damage_type: str,
    current_hp_pct: float,
) -> tuple[int, bool, dict]:
    """raw 데미지 → 회피·방어·특성 적용된 최종 데미지.
    반환: (final_damage, evaded, info dict)
      info: {"armor": int, "armor_break_applied": bool, "cap_applied": bool}
    """
    info = {"armor": 0, "armor_break_applied": False, "cap_applied": False}

    # 1) 회피 굴림 — 빗나가면 0 데미지
    if boss.evasion > 0 and random.random() < boss.evasion:
        return 0, True, info

    # 2) 방어력 선택
    armor_map = {
        DMG_PHYS: boss.armor_physical,
        DMG_ELEM: boss.armor_elemental,
        DMG_HOLY: boss.armor_holy,
    }
    armor = armor_map.get(damage_type, 0)

    # 3) armor_break 특성: HP 낮을 때 방어력 감소
    ab = boss.traits.get("armor_break")
    if ab is not None and current_hp_pct <= ab.get("at_pct", 0.30):
        armor = int(armor * ab.get("mult", 0.5))
        info["armor_break_applied"] = True

    info["armor"] = armor

    # 4) 비례 감소 식: damage * (1 − armor/(armor+100))
    if armor > 0:
        reduction = armor / (armor + 100)
        damage = int(round(raw_damage * (1 - reduction)))
    else:
        damage = int(raw_damage)

    # 5) damage_cap 특성: 단일 데미지 상한
    dc = boss.traits.get("damage_cap")
    if dc is not None:
        cap = int(dc.get("value", 9_999_999))
        if damage > cap:
            damage = cap
            info["cap_applied"] = True

    return max(1, damage), False, info


# ───────── 마나 정책 (§5.3) ─────────
def max_mana(int_pt: int) -> int:
    return 100 + int(int_pt)


def mana_regen_per_sec(int_pt: int) -> float:
    return (5 + int(int_pt) * 0.1) / 60.0


def calc_cooldown(agi_pt: int) -> float:
    """평타 쿨다운(초). 기획서 §5.2."""
    return max(15.0, 60.0 * (1 - agi_pt * 0.003))


def make_hp_bar(cur: int, mx: int, width: int = 14) -> str:
    if mx <= 0:
        return "▱" * width
    filled = min(width, max(0, round(width * cur / mx)))
    return "▰" * filled + "▱" * (width - filled)


def _fmt_remaining(seconds: int) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    return f"{m:02d}:{s:02d}"


# ───────── 개인 패널 View (D7) — Phase 2: 평타 + 4스킬 ─────────
class RaidPanelView(discord.ui.View):
    """본인 ephemeral 패널. 평타 1개 + 스킬 4개 = 총 5버튼.
    버튼은 본인만 동작(`interaction.user.id` 검증).
    """

    def __init__(
        self, cog: "Raid", user_id: int, raid_id: int,
        learned_skills: Optional[set[str]] = None,
    ) -> None:
        super().__init__(timeout=PANEL_TIMEOUT)
        self.cog = cog
        self.user_id = user_id
        self.raid_id = raid_id
        self._interaction: Optional[discord.Interaction] = None
        self.learned_skills: set[str] = set(learned_skills or set())
        # 동적 버튼 구성 — 평타는 항상, 스킬은 학습한 것만 (비밀 정책)
        self._build_buttons()

    def _build_buttons(self) -> None:
        # row 0: 평타 (항상 표시, 비용 없음)
        atk = discord.ui.Button(
            label="⚔️ 평타", style=discord.ButtonStyle.primary,
            custom_id="raid_atk", row=0,
        )
        atk.callback = self._make_action_cb("atk")  # type: ignore[assignment]
        self.add_item(atk)
        # row 1: 학습된 스킬만 노출
        for key, sk in SKILLS.items():
            if key not in self.learned_skills:
                continue   # 미해방은 버튼 자체가 없음(비밀)
            btn = discord.ui.Button(
                label=f"{sk.emoji} {sk.name} ({sk.mana_cost}MP)",
                style=discord.ButtonStyle.secondary,
                custom_id=f"raid_skill_{key}", row=1,
            )
            btn.callback = self._make_action_cb(key)  # type: ignore[assignment]
            self.add_item(btn)

    def _make_action_cb(self, action_key: str):
        async def _cb(interaction: discord.Interaction) -> None:
            await self.cog.handle_action(interaction, self, action_key=action_key)
        return _cb

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "본인 패널만 사용할 수 있어요. `/레이드참가` 를 입력하세요.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self._interaction is not None:
            try:
                await self._interaction.edit_original_response(
                    content="⏱️ 패널이 만료됐어요. `/레이드참가` 를 다시 입력하세요.",
                    view=self,
                )
            except discord.HTTPException:
                pass

    def refresh_button_states(
        self,
        *,
        atk_cd_left: float,
        mana: float,
        skill_cds_left: dict[str, float],
    ) -> None:
        """버튼 활성/비활성을 현재 상태로 갱신. 패널 edit 직전에 호출."""
        for child in self.children:
            if not isinstance(child, discord.ui.Button):
                continue
            cid = child.custom_id or ""
            if cid == "raid_atk":
                child.disabled = atk_cd_left > 0
            elif cid.startswith("raid_skill_"):
                key = cid[len("raid_skill_"):]
                skill = SKILLS.get(key)
                if skill is None:
                    continue
                child.disabled = (
                    mana < skill.mana_cost or skill_cds_left.get(key, 0.0) > 0
                )

    # (구버전 5 고정 데코레이터 버튼은 제거됨 — _build_buttons() 에서 동적 추가)


# ───────── 메인 코그 ─────────
class Raid(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.guild_id = bot.settings.guild_id

        # 인메모리 상태 — 봇 재시작 시 비워짐(쿨다운·마나만 일시 영향, MVP 수용)
        self._atk_cd: dict[int, float] = {}            # user_id → next_attack monotonic
        self._skill_cd: dict[int, dict[str, float]] = {}  # user_id → {skill_key: next_use_monotonic}
        self._mana: dict[int, float] = {}              # user_id → current mana
        self._mana_last_update: dict[int, float] = {}  # user_id → monotonic
        self._embed_dirty: bool = False
        self._last_action_at: float = 0.0             # monotonic
        self._last_embed_edit: float = 0.0
        self._raid_lock = asyncio.Lock()              # 데미지 적용 직렬화
        # 진행 중인 레이드 id 메모리 캐시 — 매 초 DB 조회 방지(Neon 친화)
        # cog_load 에서 복구, summon_raid 에서 set, _end_raid 에서 clear
        self._active_raid_id: Optional[int] = None

    # ------------------------------------------------------------ 마나 헬퍼
    def _refresh_mana(self, user_id: int, stats: dict) -> tuple[float, int]:
        """(현재 마나, 최대 마나). 호출 시점에 자연재생 갱신."""
        mx = max_mana(stats["int_pt"])
        regen = mana_regen_per_sec(stats["int_pt"])
        now = time.monotonic()
        last = self._mana_last_update.get(user_id, now)
        cur = self._mana.get(user_id, float(mx))     # 첫 진입 = 풀마나
        elapsed = max(0.0, now - last)
        cur = min(float(mx), cur + elapsed * regen)
        self._mana[user_id] = cur
        self._mana_last_update[user_id] = now
        return cur, mx

    def _consume_mana(self, user_id: int, amount: int) -> None:
        self._mana[user_id] = max(0.0, self._mana.get(user_id, 0.0) - float(amount))

    def _atk_cd_left(self, user_id: int) -> float:
        return max(0.0, self._atk_cd.get(user_id, 0.0) - time.monotonic())

    def _skill_cds_left(self, user_id: int) -> dict[str, float]:
        cds = self._skill_cd.get(user_id, {})
        now = time.monotonic()
        return {k: max(0.0, v - now) for k, v in cds.items()}

    def _set_skill_cd(self, user_id: int, skill_key: str, seconds: float) -> None:
        self._skill_cd.setdefault(user_id, {})[skill_key] = time.monotonic() + seconds

    # ------------------------------------------------------------ 페이즈 헬퍼
    def _phase_def(self, boss: BossDef, phase: int) -> Optional[PhaseDef]:
        """phase 1 = 시작 페이즈(없음). phase 2 = boss.phases[0]."""
        idx = phase - 2
        if 0 <= idx < len(boss.phases):
            return boss.phases[idx]
        return None

    def _phase_mult(self, boss: BossDef, phase: int) -> float:
        pd = self._phase_def(boss, phase)
        return pd.damage_taken_mult if pd is not None else 1.0

    def _phase_color(self, boss: BossDef, phase: int) -> discord.Color:
        pd = self._phase_def(boss, phase)
        return pd.color if pd is not None else boss.color

    def _next_phase(self, boss: BossDef, current_phase: int, after_hp: int, max_hp: int) -> Optional[int]:
        """HP 비율이 다음 페이즈 임계에 도달했으면 새 phase 번호 반환."""
        idx = current_phase - 1  # 다음 phase 의 boss.phases 인덱스
        if idx >= len(boss.phases):
            return None
        threshold = boss.phases[idx].hp_pct
        if after_hp / max(1, max_hp) <= threshold:
            return current_phase + 1
        return None

    async def cog_load(self) -> None:
        log.info(
            "Raid cog 로드 — Phase 3.5 (보스 %d종, 특성 %d종, 데미지타입 3, 디바운스 %.0fs/캡 %.0fs/idle %.0fs)",
            len(BOSSES), len(TRAITS), EMBED_DEBOUNCE, EMBED_HARD_CAP, IDLE_REFRESH,
        )
        # 봇 재시작 시 진행 중이던 active 레이드 복구 (id 만 캐시)
        try:
            row = await self.db.get_active_raid(self.guild_id)
            if row is not None:
                self._active_raid_id = int(row["id"])
                # 처음 1회는 즉시 갱신되도록 last_embed_edit 안 건드림 → 0.0 이라서 즉시 idle tick
                log.info("active 레이드 복구: id=%s", self._active_raid_id)
        except Exception:  # noqa: BLE001
            log.warning("active 레이드 복구 실패", exc_info=True)
        self.embed_update_loop.start()
        self.timeout_loop.start()
        self.regen_loop.start()

    async def cog_unload(self) -> None:
        self.embed_update_loop.cancel()
        self.timeout_loop.cancel()
        self.regen_loop.cancel()

    # ------------------------------------------------------------ 전용 채널 ensure/생성
    async def _ensure_raid_channel(
        self, guild: discord.Guild
    ) -> tuple[Optional[discord.TextChannel], Optional[str]]:
        """레이드 전용 채널을 확보. 순서:
        1) `guild_config.raid_channel_id` 가 가리키는 채널이 살아있으면 그대로 사용
        2) 이름으로 `보스레이드` 채널이 존재하면 그것 채택(+ DB 동기화)
        3) 봇이 직접 생성(`manage_channels` 필요)
        반환: (채널 or None, 에러 메시지 or None).
        """
        # 1) 저장된 ID
        try:
            cid = await self.db.get_raid_channel(guild.id)
        except Exception:  # noqa: BLE001
            cid = None
        if cid:
            ch = guild.get_channel(cid)
            if isinstance(ch, discord.TextChannel):
                return ch, None

        # 2) 이름 기반 발견(재배포·재시작으로 ID 분실 대비)
        existing = discord.utils.get(guild.text_channels, name=RAID_CHANNEL_NAME)
        if existing is not None:
            try:
                await self.db.set_raid_channel(guild.id, existing.id)
            except Exception:  # noqa: BLE001
                log.debug("raid_channel_id 동기화 실패", exc_info=True)
            return existing, None

        # 3) 봇이 직접 생성
        me = guild.me
        if me is None or not me.guild_permissions.manage_channels:
            return None, (
                "봇에 **채널 관리** 권한이 없어 자동 생성에 실패했어요.\n"
                f"관리자가 `{RAID_CHANNEL_NAME}` 채널을 만든 뒤 `/레이드채널설정` 으로 지정해 주세요."
            )
        try:
            ch = await guild.create_text_channel(
                name=RAID_CHANNEL_NAME,
                topic=RAID_CHANNEL_TOPIC,
                reason="레이드 시스템: 전용 채널 자동 생성",
            )
        except discord.Forbidden:
            return None, "권한 부족으로 채널을 만들 수 없어요."
        except discord.HTTPException as exc:
            return None, f"채널 생성 중 오류: {exc}"

        try:
            await self.db.set_raid_channel(guild.id, ch.id)
        except Exception:  # noqa: BLE001
            log.warning("새 raid_channel_id 저장 실패", exc_info=True)
        log.info("레이드 전용 채널 자동 생성: #%s (%s)", ch.name, ch.id)
        return ch, None

    # ------------------------------------------------------------ 헬퍼
    async def _get_active(self) -> Optional[dict]:
        try:
            row = await self.db.get_active_raid(self.guild_id)
        except Exception:  # noqa: BLE001
            return None
        return dict(row) if row is not None else None

    async def _get_user_stats(self, user_id: int) -> dict:
        try:
            row = await self.db.get_user_stats(self.guild_id, user_id)
        except Exception:  # noqa: BLE001
            row = None
        if row is None:
            return {"str_pt": 0, "agi_pt": 0, "int_pt": 0, "luk_pt": 0}
        return {k: int(row[k]) for k in ("str_pt", "agi_pt", "int_pt", "luk_pt")}

    # ------------------------------------------------------------ 스킬 해방 체크/알림
    async def check_skill_unlocks(
        self,
        guild_id: int,
        user_id: int,
        notify_user: Optional[discord.abc.User] = None,
    ) -> list[str]:
        """현재 레벨/스탯으로 새로 충족되는 스킬을 학습 처리.
        notify_user 가 주어지면 새 해방분만 DM 으로 안내(실패해도 학습은 유지).
        반환: 이번에 새로 해방된 skill_key 리스트.
        """
        try:
            xp = await self.db.get_user_xp(guild_id, user_id)
        except Exception:  # noqa: BLE001
            return []
        lv, _, _ = level_from_xp(xp)
        stats = await self._get_user_stats(user_id)
        try:
            learned = await self.db.get_learned_skills(guild_id, user_id)
        except Exception:  # noqa: BLE001
            return []
        newly: list[str] = []
        for key, sk in SKILLS.items():
            if key in learned:
                continue
            if not check_requirement(sk.requirements, lv, stats):
                continue
            try:
                ok = await self.db.learn_skill(guild_id, user_id, key)
            except Exception:  # noqa: BLE001
                log.warning("스킬 학습 저장 실패 uid=%s key=%s", user_id, key, exc_info=True)
                continue
            if ok:
                newly.append(key)
        if newly and notify_user is not None and not GUARD.is_paused():
            await self._dm_new_skills(notify_user, newly)
        return newly

    async def _dm_new_skills(
        self, user: discord.abc.User, skill_keys: list[str]
    ) -> None:
        """DM 으로 새로 해방된 스킬 안내. 실패하면 조용히 넘김(차단 등)."""
        embeds: list[discord.Embed] = []
        for key in skill_keys:
            sk = SKILLS.get(key)
            if sk is None:
                continue
            e = discord.Embed(
                title=f"🎉 새 스킬 해방!  {sk.emoji} {sk.name}",
                description=sk.description,
                color=discord.Color.gold(),
                timestamp=datetime.now(timezone.utc),
            )
            e.add_field(
                name="비용 / 쿨다운",
                value=f"마나 **{sk.mana_cost}** · 쿨다운 **{int(sk.cooldown.total_seconds())}초**",
                inline=False,
            )
            e.set_footer(
                text="레이드 채널에서 /레이드참가 → 본인 패널의 새 버튼으로 사용 가능"
            )
            embeds.append(e)
        if not embeds:
            return
        try:
            for emb in embeds:
                await user.send(embed=emb)
        except discord.Forbidden:
            log.info("DM 차단으로 스킬 해방 알림 실패 uid=%s", user.id)
        except discord.HTTPException:
            log.debug("스킬 해방 DM 실패", exc_info=True)

    async def _build_panel_embed(
        self, member: discord.Member, raid: dict, stats: dict
    ) -> discord.Embed:
        # 레벨
        try:
            xp = await self.db.get_user_xp(self.guild_id, member.id)
        except Exception:  # noqa: BLE001
            xp = 0
        lv, _, _ = level_from_xp(xp)
        # 누적 데미지
        my_damage = 0
        try:
            parts = await self.db.get_raid_top_n(raid["id"], n=999)
            for p in parts:
                if int(p["user_id"]) == member.id:
                    my_damage = int(p["total_damage"])
                    break
        except Exception:  # noqa: BLE001
            pass

        embed = discord.Embed(
            title=f"📜 내 상태창 — {member.display_name}",
            color=discord.Color.dark_purple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="레벨", value=f"**{lv}** / {MAX_LEVEL}", inline=True)
        embed.add_field(
            name="스탯",
            value=(
                f"💪 STR `{stats['str_pt']}`  💨 AGI `{stats['agi_pt']}`\n"
                f"🧠 INT `{stats['int_pt']}`  🍀 LUK `{stats['luk_pt']}`"
            ),
            inline=True,
        )
        # 마나바
        mana, mx_mana = self._refresh_mana(member.id, stats)
        embed.add_field(
            name=f"🧪 마나  {int(mana)} / {mx_mana}",
            value=f"`{make_hp_bar(int(mana), mx_mana)}`",
            inline=False,
        )
        # 평타 쿨다운
        atk_cd = self._atk_cd_left(member.id)
        atk_text = "⚔️ 즉시 사용 가능" if atk_cd <= 0 else f"⏳ {atk_cd:.0f}초 남음"
        embed.add_field(name="평타", value=atk_text, inline=False)
        # 스킬 상태 — 학습한 것만 표시(미학습은 비밀)
        try:
            learned = await self.db.get_learned_skills(self.guild_id, member.id)
        except Exception:  # noqa: BLE001
            learned = set()
        cds = self._skill_cds_left(member.id)
        sk_lines: list[str] = []
        for key, sk in SKILLS.items():
            if key not in learned:
                continue
            cd_left = cds.get(key, 0.0)
            if cd_left > 0:
                status = f"⏳ {cd_left:.0f}초"
            elif mana < sk.mana_cost:
                status = "🚫 마나 부족"
            else:
                status = "✅ 사용 가능"
            sk_lines.append(f"{sk.emoji} **{sk.name}** ({sk.mana_cost}MP) — {status}")
        if sk_lines:
            embed.add_field(name="학습된 스킬", value="\n".join(sk_lines), inline=False)
        else:
            embed.add_field(
                name="스킬",
                value="_아직 해방된 스킬이 없어요. 레벨·스탯을 올리면 자동으로 해방됩니다._",
                inline=False,
            )
        embed.add_field(name="누적 데미지", value=f"`{my_damage:,}`", inline=True)
        embed.set_footer(text="패널 유효 ~14분 · 만료 시 /레이드참가 다시 입력")
        return embed

    async def _build_live_embed(self, raid: dict) -> discord.Embed:
        boss = BOSSES.get(raid["boss_key"])
        if boss is None:
            return discord.Embed(title="?", description="알 수 없는 보스")

        elapsed = (datetime.now(timezone.utc) - raid["started_at"]).total_seconds()
        remaining = int(boss.time_limit.total_seconds() - elapsed)
        hp_bar = make_hp_bar(int(raid["current_hp"]), int(raid["max_hp"]))
        pct = 100.0 * raid["current_hp"] / max(1, raid["max_hp"])
        phase = int(raid.get("phase", 1))
        phase_color = self._phase_color(boss, phase)
        phase_def = self._phase_def(boss, phase)
        phase_label = f"  [페이즈 {phase}: {phase_def.name}]" if phase_def is not None else ""

        embed = discord.Embed(
            title=f"{boss.emoji} {boss.name}  Lv {boss.level}{phase_label}",
            color=phase_color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.description = (
            f"`{hp_bar}`\n"
            f"HP **{int(raid['current_hp']):,}** / {int(raid['max_hp']):,}  ({pct:.1f}%)\n"
            f"시간 남음: **{_fmt_remaining(remaining)}**"
        )
        # 보스 이미지 (raids.image_url 우선, 없으면 boss.image_url 폴백)
        img_url = (raid.get("image_url") or boss.image_url or "").strip()
        if img_url:
            embed.set_image(url=img_url)
        thumb_url = (raid.get("thumbnail_url") or boss.thumbnail_url or "").strip()
        if thumb_url:
            embed.set_thumbnail(url=thumb_url)

        # 참가자 / 누적 데미지
        try:
            parts = await self.db.get_raid_top_n(raid["id"], n=4)
            cnt = await self.db.count_raid_participants(raid["id"])
        except Exception:  # noqa: BLE001
            parts, cnt = [], 0
        total_dmg = int(raid["max_hp"]) - int(raid["current_hp"])
        embed.add_field(
            name="현황",
            value=f"참가자 **{cnt}명**  ·  누적 데미지 **{total_dmg:,}**",
            inline=False,
        )

        # 보스 방어/회피 (한 줄 요약)
        def _pct(arm: int) -> str:
            if arm <= 0:
                return "—"
            return f"{int(100 * arm / (arm + 100))}%"
        ev_text = f"{int(boss.evasion * 100)}%" if boss.evasion > 0 else "—"
        embed.add_field(
            name="방어 / 회피",
            value=(
                f"⚔️ 물리 `{_pct(boss.armor_physical)}`  "
                f"🔮 속성 `{_pct(boss.armor_elemental)}`  "
                f"✨ 신성 `{_pct(boss.armor_holy)}`\n"
                f"💨 회피 `{ev_text}`"
            ),
            inline=False,
        )

        # 보스 특성 (있을 때만)
        if boss.traits:
            trait_lines: list[str] = []
            for key, params in boss.traits.items():
                td = TRAITS.get(key)
                if td is None:
                    continue
                extra = ""
                if key == "damage_cap":
                    extra = f" ({params.get('value', '?')})"
                elif key == "regen":
                    extra = f" ({params.get('per_min', '?')}/분)"
                elif key == "phase_heal":
                    extra = f" ({int(params.get('pct', 0) * 100)}%)"
                trait_lines.append(f"{td.emoji} **{td.name}**{extra} — {td.description}")
            embed.add_field(name="특성", value="\n".join(trait_lines), inline=False)

        # 최근 5턴 로그
        try:
            recent = await self.db.recent_raid_actions(raid["id"], limit=EMBED_LOG_LINES)
        except Exception:  # noqa: BLE001
            recent = []
        if recent:
            log_lines: list[str] = []
            for r in recent:
                uid = r["user_id"]
                action = r["action"]
                damage = r["damage"] or 0
                crit = r["crit"]
                weakness = r["weakness"]
                tags = []
                if crit:
                    tags.append("치명!")
                if weakness:
                    tags.append("약점!")
                tag = f" ({', '.join(tags)})" if tags else ""
                if action == "attack":
                    log_lines.append(f"• <@{uid}> 평타 ⚔️ −{damage:,}{tag}")
                elif action.startswith("skill:"):
                    sk = SKILLS.get(action[len("skill:"):])
                    if sk is not None:
                        log_lines.append(f"• <@{uid}> {sk.name} {sk.emoji} −{damage:,}{tag}")
                    else:
                        log_lines.append(f"• <@{uid}> 스킬 −{damage:,}{tag}")
                elif action.startswith("phase:"):
                    name = action[len("phase:"):]
                    log_lines.append(f"• ⚡ **페이즈 전환 — {name}**")
                elif action == "phase_heal":
                    log_lines.append(f"• 🩸 **페이즈 회생** — 보스 HP +{damage:,}")
                elif action == "boss_flavor":
                    log_lines.append(f"• {boss.flavor_lines[damage % len(boss.flavor_lines)]}")
                else:
                    log_lines.append(f"• <@{uid}> {action} −{damage:,}")
            embed.add_field(name="📜 최근 행동", value="\n".join(log_lines), inline=False)

        # TOP 데미지
        if parts:
            medals = {1: "🥇", 2: "🥈", 3: "🥉"}
            tops: list[str] = []
            for i, p in enumerate(parts, start=1):
                head = medals.get(i, f"#{i}")
                tops.append(f"{head} <@{int(p['user_id'])}> — {int(p['total_damage']):,}")
            embed.add_field(name="TOP 데미지", value="\n".join(tops), inline=False)

        # 참여 방법 안내 (임베드 본문 마지막 필드) — 스킬 이모지는 SKILLS 에서 동적 산출
        skill_emojis = "".join(sk.emoji for sk in SKILLS.values())
        embed.add_field(
            name="📢 참여 방법 — `/레이드참가`",
            value=(
                "🎮 **`/레이드참가`** 를 입력하면 본인 전용 전투 패널이 열립니다.\n"
                f"ㆍ 패널의 ⚔️ **평타** 또는 {skill_emojis} **스킬** 버튼으로 보스 공격\n"
                "ㆍ 본인만 보이는 ephemeral 패널 — 약 14분 후 만료 시 다시 입력\n"
                "ㆍ 한 번이라도 행동 → **데미지 비율**로 보상 정산 (위로 보상 포함)\n"
                "🏆 결정타를 친 유저는 **별도 룰렛 1회** 추가 굴림 (희귀 ↑)"
            ),
            inline=False,
        )
        embed.set_footer(text="이 채널 외에서 입력하면 동작하지 않습니다.")
        return embed

    def _mark_dirty(self) -> None:
        self._embed_dirty = True
        self._last_action_at = time.monotonic()

    # ------------------------------------------------------------ 라이브 임베드 갱신 루프
    @tasks.loop(seconds=1)
    async def embed_update_loop(self) -> None:
        # active 레이드 없으면 DB 조회 없이 즉시 종료 (Neon 친화)
        if self._active_raid_id is None:
            self._embed_dirty = False
            return
        now = time.monotonic()
        # 갱신 트리거 결정:
        #  (A) dirty (액션 발생) + 디바운스 + 하드 캡 통과
        #  (B) idle 이지만 마지막 edit 으로부터 IDLE_REFRESH 경과 → 시간 카운트 갱신
        do_update = False
        if self._embed_dirty:
            if (now - self._last_action_at >= EMBED_DEBOUNCE
                    and now - self._last_embed_edit >= EMBED_HARD_CAP):
                do_update = True
        elif now - self._last_embed_edit >= IDLE_REFRESH:
            do_update = True
        if not do_update:
            return
        if GUARD.is_paused():
            return
        # 여기서만 DB 조회 (1초마다 X)
        raid = await self._get_active()
        if raid is None or raid.get("message_id") is None:
            self._embed_dirty = False
            self._active_raid_id = None
            return
        channel = self.bot.get_channel(int(raid["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            msg = await channel.fetch_message(int(raid["message_id"]))
            embed = await self._build_live_embed(raid)
            await msg.edit(embed=embed, allowed_mentions=SILENT)
            self._embed_dirty = False
            self._last_embed_edit = now
        except discord.NotFound:
            log.warning("라이브 임베드 메시지 사라짐 (raid_id=%s)", raid["id"])
            self._embed_dirty = False
        except discord.HTTPException as exc:
            log.warning("라이브 임베드 갱신 실패: %s", exc)

    @embed_update_loop.before_loop
    async def _before_embed_loop(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------ 시간 초과 감시
    @tasks.loop(seconds=TIMEOUT_CHECK)
    async def timeout_loop(self) -> None:
        raid = await self._get_active()
        if raid is None:
            return
        boss = BOSSES.get(raid["boss_key"])
        if boss is None:
            return
        elapsed = datetime.now(timezone.utc) - raid["started_at"]
        if elapsed >= boss.time_limit:
            log.info("레이드 시간 초과 → 패배 처리 (raid_id=%s)", raid["id"])
            await self._end_raid(raid["id"], "defeat", final_blow_user_id=None)

    @timeout_loop.before_loop
    async def _before_timeout(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------ regen 특성 처리 (10초 주기)
    @tasks.loop(seconds=10)
    async def regen_loop(self) -> None:
        raid = await self._get_active()
        if raid is None:
            return
        boss = BOSSES.get(raid["boss_key"])
        if boss is None:
            return
        regen = boss.traits.get("regen")
        if regen is None:
            return
        per_min = int(regen.get("per_min", 0))
        if per_min <= 0:
            return
        # 10초 = 1/6 분
        amount = max(1, per_min // 6)
        try:
            before, after = await self.db.apply_raid_heal(int(raid["id"]), amount)
            if after > 0 and after > before:
                self._mark_dirty()  # 라이브 임베드 갱신 트리거
        except Exception:  # noqa: BLE001
            log.debug("regen 실패", exc_info=True)

    @regen_loop.before_loop
    async def _before_regen(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------ 액션 처리 (평타 + 4스킬 통합)
    async def handle_action(
        self, interaction: discord.Interaction, view: RaidPanelView, action_key: str,
    ) -> None:
        user_id = interaction.user.id
        assert isinstance(interaction.user, discord.Member)

        # 오너 무시 모드 차단
        if not is_owner(user_id):
            try:
                ov = await self.db.get_user_override(self.guild_id, user_id)
            except Exception:  # noqa: BLE001
                ov = None
            if ov is not None and ov["mode"] == "ignore":
                await interaction.response.send_message(
                    "지금은 참가할 수 없어요.", ephemeral=True,
                )
                return

        stats = await self._get_user_stats(user_id)
        mana, _ = self._refresh_mana(user_id, stats)

        # 액션 결정 & 자원 검증
        skill: Optional[SkillDef] = None
        block_reason: Optional[str] = None
        if action_key == "atk":
            cd_left = self._atk_cd_left(user_id)
            if cd_left > 0:
                block_reason = f"⏳ 평타 쿨다운 {cd_left:.0f}초 남음"
        else:
            skill = SKILLS.get(action_key)
            if skill is None:
                await interaction.response.send_message("알 수 없는 액션.", ephemeral=True)
                return
            cd_left = self._skill_cds_left(user_id).get(action_key, 0.0)
            if cd_left > 0:
                block_reason = f"⏳ {skill.name} 쿨다운 {cd_left:.0f}초 남음"
            elif mana < skill.mana_cost:
                block_reason = f"🚫 마나 부족 ({int(mana)}/{skill.mana_cost})"

        raid = await self._get_active()
        if raid is None:
            await interaction.response.send_message("레이드가 이미 종료됐어요.", ephemeral=True)
            return

        # 자원 부족 시 패널만 갱신해서 보여줌 (Author 에 사유 표기)
        if block_reason is not None:
            new_embed = await self._build_panel_embed(interaction.user, raid, stats)
            new_embed.set_author(name=block_reason)
            view.refresh_button_states(
                atk_cd_left=self._atk_cd_left(user_id),
                mana=mana,
                skill_cds_left=self._skill_cds_left(user_id),
            )
            try:
                await interaction.response.edit_message(embed=new_embed, view=view)
            except discord.HTTPException:
                pass
            return

        # 데미지 계산 + 적용은 락 안에서
        new_phase: Optional[int] = None
        kill = False
        async with self._raid_lock:
            raid = await self._get_active()
            if raid is None or int(raid["current_hp"]) <= 0:
                await interaction.response.send_message(
                    "레이드가 이미 종료됐어요.", ephemeral=True,
                )
                return
            boss = BOSSES.get(raid["boss_key"])
            if boss is None:
                await interaction.response.send_message("보스 정의 누락.", ephemeral=True)
                return
            phase = int(raid.get("phase", 1))
            phase_mult = self._phase_mult(boss, phase)
            cur_hp_pct = int(raid["current_hp"]) / max(1, int(raid["max_hp"]))
            damage_type = DMG_PHYS if skill is None else skill.damage_type

            # 평타 또는 스킬 효과 적용 (raw → 회피/방어/특성 → final)
            total_dmg = 0
            any_crit = False
            any_weak = False
            any_evaded = False
            any_capped = False
            any_armor_break = False
            hits_count = 1 if skill is None else skill.hits
            for _ in range(hits_count):
                if skill is None:
                    r = calc_attack(
                        stats, formula=ATK_FORMULA,
                        phase_mult=phase_mult, boss_traits=boss.traits,
                    )
                else:
                    r = calc_attack(
                        stats, formula=skill.formula,
                        multiplier=skill.multiplier,
                        force_crit=skill.force_crit,
                        force_weakness=skill.force_weakness,
                        phase_mult=phase_mult,
                        boss_traits=boss.traits,
                    )
                # 회피·방어·특성 적용
                final_dmg, evaded, info = apply_defense(
                    r["damage"], boss, damage_type, cur_hp_pct,
                )
                if evaded:
                    any_evaded = True
                    continue  # 0 데미지
                total_dmg += final_dmg
                any_crit = any_crit or r["crit"]
                any_weak = any_weak or r["weakness"]
                any_capped = any_capped or info["cap_applied"]
                any_armor_break = any_armor_break or info["armor_break_applied"]

            before_hp, after_hp = await self.db.apply_raid_damage(int(raid["id"]), total_dmg)
            if before_hp == 0 and after_hp == 0:
                await interaction.response.send_message(
                    "레이드가 이미 종료됐어요.", ephemeral=True,
                )
                return

            await self.db.add_participant_damage(
                int(raid["id"]), user_id, total_dmg, is_skill=(skill is not None),
            )
            action_name = "attack" if skill is None else f"skill:{skill.key}"
            await self.db.log_raid_action(
                int(raid["id"]), user_id, action_name,
                damage=total_dmg, crit=any_crit, weakness=any_weak,
            )

            # 쿨다운/마나 갱신
            if skill is None:
                self._atk_cd[user_id] = time.monotonic() + calc_cooldown(stats["agi_pt"])
            else:
                self._consume_mana(user_id, skill.mana_cost)
                # 스킬 쿨다운도 AGI 보정 (최대 75% 감소까지)
                base_sk_cd = skill.cooldown.total_seconds()
                sk_cd = max(base_sk_cd * 0.25, base_sk_cd * (1 - stats["agi_pt"] * 0.003))
                self._set_skill_cd(user_id, skill.key, sk_cd)

            self._mark_dirty()
            new_phase = self._next_phase(boss, phase, after_hp, int(raid["max_hp"]))
            kill = after_hp <= 0

        # 페이즈 전환 (락 밖, DB)
        if new_phase is not None and not kill:
            try:
                await self.db.set_raid_phase(int(raid["id"]), new_phase)
                phase_def = self._phase_def(boss, new_phase)
                if phase_def is not None:
                    await self.db.log_raid_action(
                        int(raid["id"]), None, f"phase:{phase_def.name}",
                        damage=None, crit=False, weakness=False,
                    )
                # phase_heal 특성: 페이즈 전환 시 HP 회복
                ph = boss.traits.get("phase_heal")
                if ph is not None:
                    pct = float(ph.get("pct", 0.10))
                    heal_amount = int(int(raid["max_hp"]) * pct)
                    if heal_amount > 0:
                        try:
                            _, new_hp = await self.db.apply_raid_heal(
                                int(raid["id"]), heal_amount,
                            )
                            await self.db.log_raid_action(
                                int(raid["id"]), None, "phase_heal",
                                damage=heal_amount, crit=False, weakness=False,
                            )
                            log.info(
                                "페이즈 회생: +%d HP (raid_id=%s, new_hp=%d)",
                                heal_amount, raid["id"], new_hp,
                            )
                        except Exception:  # noqa: BLE001
                            log.warning("phase_heal 실패", exc_info=True)
            except Exception:  # noqa: BLE001
                log.warning("페이즈 전환 실패", exc_info=True)

        # 패널 갱신
        view._interaction = interaction
        raid_view = await self._get_active() or dict(raid)
        new_embed = await self._build_panel_embed(interaction.user, raid_view, stats)
        action_label = "⚔️ 평타" if skill is None else f"{skill.emoji} {skill.name}"
        tag_parts = []
        if any_crit:
            tag_parts.append("치명!")
        if any_weak:
            tag_parts.append("약점!")
        if any_armor_break:
            tag_parts.append("갑주균열!")
        if any_capped:
            tag_parts.append("상한")
        if any_evaded:
            tag_parts.append("일부 회피")
        tag = f" ({', '.join(tag_parts)})" if tag_parts else ""
        if total_dmg == 0 and any_evaded:
            new_embed.set_author(name=f"{action_label} 회피됨! → 남은 HP {after_hp:,}")
        else:
            new_embed.set_author(
                name=f"{action_label} −{total_dmg:,}{tag}  → 남은 HP {after_hp:,}"
            )
        new_mana, _ = self._refresh_mana(user_id, stats)
        view.refresh_button_states(
            atk_cd_left=self._atk_cd_left(user_id),
            mana=new_mana,
            skill_cds_left=self._skill_cds_left(user_id),
        )
        try:
            await interaction.response.edit_message(embed=new_embed, view=view)
        except discord.HTTPException as exc:
            log.warning("패널 갱신 실패: %s", exc)

        # 결정타 시 종료 처리 (인터랙션 응답 후에 백그라운드로)
        if kill:
            await self._end_raid(int(raid["id"]), "victory", final_blow_user_id=user_id)

    # ------------------------------------------------------------ 종료 처리
    async def _end_raid(
        self, raid_id: int, status: str, final_blow_user_id: Optional[int]
    ) -> None:
        # DB 종료 (멱등 — 이미 종료된 경우 noop)
        try:
            await self.db.end_raid(raid_id, status, final_blow_user_id)
        except Exception:  # noqa: BLE001
            log.warning("레이드 종료 DB 갱신 실패", exc_info=True)
            return

        raid = await self.db.get_raid(raid_id)
        if raid is None:
            return
        boss = BOSSES.get(raid["boss_key"])
        if boss is None:
            return

        # 보상 계산 + 적용
        try:
            participants = await self.db.get_raid_participants(raid_id)
        except Exception:  # noqa: BLE001
            participants = []

        rewards: list[dict] = []
        for p in participants:
            uid = int(p["user_id"])
            damage = int(p["total_damage"])
            total_team_dmg = sum(int(x["total_damage"]) for x in participants) or 1
            proportion = damage / total_team_dmg
            if status == "victory":
                xp = int(round(boss.base_xp * 0.5 + boss.base_xp * 3 * proportion))
            else:
                # 패배 위로 (D9)
                xp = int(round(boss.base_xp * 0.2))
            # 결정타 보너스 (D6)
            if p["final_blow"]:
                xp += boss.final_blow_xp
            if xp <= 0:
                continue
            # XP 적용 (voice 와 동일 누적 패턴, 부스트·INT 보너스 미적용)
            try:
                before = await self.db.get_user_xp(self.guild_id, uid)
                await self.db.add_voice_xp(self.guild_id, uid, xp)
                after = before + xp
            except Exception:  # noqa: BLE001
                log.warning("레이드 보상 XP 적립 실패 uid=%s", uid, exc_info=True)
                continue
            before_lv, _, _ = level_from_xp(before)
            after_lv, _, _ = level_from_xp(after)
            rewards.append({
                "user_id": uid, "damage": damage, "proportion": proportion,
                "xp": xp, "final_blow": bool(p["final_blow"]),
                "level_up": after_lv > before_lv, "new_level": after_lv,
            })

        # 결과 임베드 발송 (XP 보상 표시)
        await self._send_result_embed(raid, boss, rewards, status)

        # 드롭 계산 + 인벤토리 입금 (승리 시에만 실제 드롭)
        try:
            drops = await self._award_drops(boss, participants, final_blow_user_id, status)
        except Exception:  # noqa: BLE001
            log.warning("드롭 계산 실패", exc_info=True)
            drops = {}

        # 라이브 임베드 unpin (실패해도 무시)
        channel = self.bot.get_channel(int(raid["channel_id"]))
        if isinstance(channel, discord.TextChannel):
            if raid.get("message_id"):
                try:
                    msg = await channel.fetch_message(int(raid["message_id"]))
                    if msg.pinned:
                        await msg.unpin()
                except discord.HTTPException:
                    pass
            # 룰렛 연출 (드롭 있을 때만)
            if drops:
                try:
                    await self._send_roulette(channel, drops, final_blow_user_id)
                except Exception:  # noqa: BLE001
                    log.warning("룰렛 발송 실패", exc_info=True)

        # 인메모리 정리 (다음 레이드에서 풀마나·풀쿨다운으로 재시작)
        self._atk_cd.clear()
        self._skill_cd.clear()
        self._mana.clear()
        self._mana_last_update.clear()
        self._embed_dirty = False
        self._active_raid_id = None

    # ------------------------------------------------------------ 드롭 + 룰렛 (Phase 3)
    async def _award_drops(
        self,
        boss: BossDef,
        participants: list,
        final_blow_user_id: Optional[int],
        status: str,
    ) -> dict[int, list[str]]:
        """드롭 계산 + 인벤토리 입금. 반환: {user_id: [item_key, ...]}.

        분배 규칙(Phase 3):
        - 승리(victory) 만 드롭. 패배 시 위로 XP 만 지급.
        - 데미지 > 0 인 참가자만 대상(데미지 0 = 보상 0 — D13)
        - 순위별 보장 확률: TOP1 100% / TOP2 60% / TOP3 40% / TOP4+ 20%
        - 결정타: 별도 final_blow_table 에서 추가 1회 보장 굴림 (희귀 가중↑)
        """
        drops: dict[int, list[str]] = {}
        if status != "victory":
            return drops
        # 데미지 > 0 만 정렬
        active = [p for p in participants if int(p["total_damage"]) > 0]
        for idx, p in enumerate(active):
            uid = int(p["user_id"])
            if idx == 0:
                prob = 1.0
            elif idx == 1:
                prob = 0.6
            elif idx == 2:
                prob = 0.4
            else:
                prob = 0.2
            if random.random() < prob:
                key = roll_drop(boss.drop_table)
                if key is not None:
                    drops.setdefault(uid, []).append(key)
        # 결정타 별도 풀
        if final_blow_user_id is not None:
            pool = boss.final_blow_table or boss.drop_table
            key = roll_drop(pool)
            if key is not None:
                drops.setdefault(final_blow_user_id, []).append(key)
        # 인벤토리 입금 (cogs.leveling.ITEMS 정의된 키만)
        from cogs.leveling import ITEMS as LV_ITEMS  # 지연 임포트 (순환 방지)
        for uid, keys in drops.items():
            for key in keys:
                if key not in LV_ITEMS:
                    log.warning("알 수 없는 드롭 키 스킵: %s", key)
                    continue
                try:
                    await self.db.add_to_inventory(self.guild_id, uid, key, 1)
                except Exception:  # noqa: BLE001
                    log.warning("드롭 입금 실패 uid=%s key=%s", uid, key, exc_info=True)
        return drops

    async def _send_roulette(
        self,
        channel: discord.TextChannel,
        drops: dict[int, list[str]],
        final_blow_user_id: Optional[int],
    ) -> None:
        """3단계 룰렛 연출 (1초 간격 edit). 가드 활성 시 단축."""
        from cogs.leveling import ITEMS as LV_ITEMS  # 지연 임포트
        if not drops:
            return
        if GUARD.is_paused():
            # 가드 중엔 한 줄 안내만
            try:
                lines = []
                for uid, keys in drops.items():
                    for key in keys:
                        it = LV_ITEMS.get(key)
                        if it:
                            lines.append(f"<@{uid}> → {it.emoji} {it.name}")
                if lines:
                    e = discord.Embed(
                        title="🎁 드롭 결과",
                        description="\n".join(lines),
                        color=discord.Color.gold(),
                    )
                    await channel.send(embed=e, allowed_mentions=SILENT)
            except discord.HTTPException:
                pass
            return

        # 1단계: 시작
        e1 = discord.Embed(
            title="🎰 보상 룰렛", description="굴리는 중...",
            color=discord.Color.gold(),
        )
        try:
            msg = await channel.send(embed=e1, allowed_mentions=SILENT)
        except discord.HTTPException:
            return
        await asyncio.sleep(1.0)
        # 2단계: 흔들림
        e2 = discord.Embed(
            title="🎰 보상 룰렛", description="굴리는 중... ✨ 두근두근 ✨",
            color=discord.Color.orange(),
        )
        try:
            await msg.edit(embed=e2)
        except discord.HTTPException:
            pass
        await asyncio.sleep(1.0)
        # 3단계: 결과 reveal
        lines: list[str] = []
        has_rare = False
        for uid, keys in drops.items():
            for key in keys:
                it = LV_ITEMS.get(key)
                if it is None:
                    continue
                rare = key in ("elixir", "lucky_charm")
                tag = ""
                if uid == final_blow_user_id and key == keys[-1]:
                    tag = "  💀 결정타 룰렛"
                rare_mark = " ✨" if rare else ""
                lines.append(f"<@{uid}> → {it.emoji} **{it.name}**{rare_mark}{tag}")
                if rare:
                    has_rare = True
        e3 = discord.Embed(
            title="🎰 보상 룰렛 결과!",
            description="\n".join(lines) if lines else "(드롭 없음)",
            color=discord.Color.purple() if has_rare else discord.Color.gold(),
            timestamp=datetime.now(timezone.utc),
        )
        if has_rare:
            e3.set_footer(text="✨ 희귀 아이템 등장!  · /인벤토리 로 확인 · /사용 으로 발동")
        else:
            e3.set_footer(text="/인벤토리 로 확인 · /사용 으로 발동")
        try:
            await msg.edit(embed=e3)
        except discord.HTTPException:
            pass

    async def _send_result_embed(
        self, raid: dict, boss: BossDef, rewards: list[dict], status: str
    ) -> None:
        channel = self.bot.get_channel(int(raid["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            return
        if GUARD.is_paused():
            log.info("1015 가드 활성 → 결과 임베드 발송 스킵 (raid_id=%s)", raid["id"])
            return

        if status == "victory":
            title = f"🏆 {boss.emoji} {boss.name} 처치!"
            color = discord.Color.green()
        elif status == "defeat":
            title = f"⏱️ {boss.emoji} {boss.name} — 시간 초과 (도망)"
            color = discord.Color.dark_gray()
        else:
            title = f"🚫 {boss.emoji} {boss.name} — 취소됨"
            color = discord.Color.dark_gray()

        embed = discord.Embed(title=title, color=color, timestamp=datetime.now(timezone.utc))
        elapsed = (raid["ended_at"] - raid["started_at"]) if raid.get("ended_at") else timedelta(0)
        total_dmg = int(raid["max_hp"]) - int(raid["current_hp"])
        embed.description = (
            f"소요 시간: **{_fmt_remaining(int(elapsed.total_seconds()))}**\n"
            f"참가자 **{len(rewards)}명**  ·  누적 데미지 **{total_dmg:,}**"
        )

        if not rewards:
            embed.add_field(name="보상", value="(참가자 없음)", inline=False)
        else:
            medals = {1: "🥇", 2: "🥈", 3: "🥉"}
            sorted_r = sorted(rewards, key=lambda r: (-r["damage"], r["user_id"]))
            lines: list[str] = []
            for i, r in enumerate(sorted_r[:10], start=1):
                head = medals.get(i, f"`#{i:>2}`")
                tag = "  💀결정타" if r["final_blow"] else ""
                lvup = f"  📈Lv{r['new_level']}" if r["level_up"] else ""
                lines.append(
                    f"{head} <@{r['user_id']}> — {r['damage']:,} ({r['proportion']*100:.1f}%)"
                    f"  +{r['xp']:,} XP{tag}{lvup}"
                )
            embed.add_field(name="기여도 / 보상", value="\n".join(lines), inline=False)
            level_ups = sum(1 for r in rewards if r["level_up"])
            footer_bits = [f"등급: {boss.tier}"]
            if level_ups:
                footer_bits.append(f"📈 레벨업 {level_ups}명")
            if status == "victory":
                footer_bits.append("🎰 보상 룰렛 발사 준비 중...")
            embed.set_footer(text="  ·  ".join(footer_bits))

        try:
            await channel.send(embed=embed, allowed_mentions=SILENT)
        except discord.HTTPException as exc:
            log.warning("결과 임베드 발송 실패: %s", exc)

    # ============================================================ 슬래시 명령
    @app_commands.command(
        name="레이드참가",
        description="진행 중인 레이드에 참가하고 본인 상태창·공격 버튼을 봅니다.",
    )
    async def join_raid(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("서버에서만 사용할 수 있어요.", ephemeral=True)
            return

        # 즉시 defer — Neon 콜드 스타트 + 다수 쿼리로 3초 초과 시 10062 방지.
        # 이후 모든 응답은 edit_original_response 로 (단일 메시지 유지 → on_timeout 도 동일 메시지 edit).
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.HTTPException:
            return  # 이미 만료/응답된 인터랙션 — 조용히 종료

        raid = await self._get_active()
        if raid is None:
            try:
                await interaction.edit_original_response(
                    content="진행 중인 레이드가 없어요. 관리자가 `/레이드소환` 으로 시작합니다.",
                )
            except discord.HTTPException:
                pass
            return

        # 전용 채널 게이트: 라이브 임베드가 있는 채널에서만 허용
        raid_ch_id = int(raid["channel_id"])
        if interaction.channel_id != raid_ch_id:
            try:
                await interaction.edit_original_response(
                    content=f"이 명령은 <#{raid_ch_id}> 채널에서만 사용할 수 있어요.",
                )
            except discord.HTTPException:
                pass
            return

        # 참가자 등록(없으면 추가)
        try:
            await self.db.join_raid(int(raid["id"]), interaction.user.id)
        except Exception:  # noqa: BLE001
            log.warning("참가 등록 실패", exc_info=True)

        stats = await self._get_user_stats(interaction.user.id)
        # 스킬 해방 체크 (실패해도 패널 표시는 진행). 새로 해방된 게 있으면 DM.
        await self.check_skill_unlocks(
            interaction.guild_id, interaction.user.id, notify_user=interaction.user,
        )
        try:
            learned = await self.db.get_learned_skills(interaction.guild_id, interaction.user.id)
        except Exception:  # noqa: BLE001
            learned = set()

        embed = await self._build_panel_embed(interaction.user, raid, stats)
        view = RaidPanelView(
            self, interaction.user.id, int(raid["id"]), learned_skills=learned,
        )
        mana, _ = self._refresh_mana(interaction.user.id, stats)
        view.refresh_button_states(
            atk_cd_left=self._atk_cd_left(interaction.user.id),
            mana=mana,
            skill_cds_left=self._skill_cds_left(interaction.user.id),
        )
        try:
            await interaction.edit_original_response(embed=embed, view=view)
            view._interaction = interaction
        except discord.HTTPException as exc:
            log.warning("레이드참가 패널 송신 실패: %s", exc)

    @app_commands.command(
        name="레이드소환",
        description="보스를 즉시 소환합니다(관리자 전용, 1마리만).",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(보스="소환할 보스")
    @app_commands.choices(보스=[
        app_commands.Choice(name=f"{b.emoji} {b.name}  Lv {b.level}  HP {b.max_hp:,}", value=k)
        for k, b in BOSSES.items()
    ])
    async def summon_raid(
        self, interaction: discord.Interaction, 보스: app_commands.Choice[str]
    ) -> None:
        if interaction.guild_id is None or interaction.guild is None:
            await interaction.response.send_message("서버에서만 사용할 수 있어요.", ephemeral=True)
            return

        existing = await self._get_active()
        if existing is not None:
            await interaction.response.send_message(
                "이미 진행 중인 레이드가 있어요. `/레이드취소` 후 다시 소환하세요.",
                ephemeral=True,
            )
            return

        boss = BOSSES.get(보스.value)
        if boss is None:
            await interaction.response.send_message("알 수 없는 보스.", ephemeral=True)
            return

        # 채널 결정: 항상 전용 채널 사용 (없으면 자동 생성)
        await interaction.response.defer(ephemeral=True)
        target_channel, err = await self._ensure_raid_channel(interaction.guild)
        if target_channel is None:
            await interaction.followup.send(err or "채널 확보 실패.", ephemeral=True)
            return

        # DB 생성
        try:
            raid_id = await self.db.create_raid(
                interaction.guild_id, boss.key, boss.max_hp, target_channel.id,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("레이드 생성 실패", exc_info=True)
            await interaction.followup.send(f"생성 실패: {exc!s}", ephemeral=True)
            return

        # 라이브 임베드 발송 (로컬 파일 업로드 처리 — 첫 송신에만)
        raid = dict(await self.db.get_raid(raid_id))
        # 로컬 파일 경로 해결 (None 이면 첨부 안 함 — boss.image_url 같은 외부 URL 만 사용)
        img_path = _resolve_asset_path(boss.image_file) if boss.image_file else None
        thumb_path = _resolve_asset_path(boss.thumbnail_file) if boss.thumbnail_file else None
        files: list[discord.File] = []
        # 임베드 빌드 전에 attachment URI 를 raid 에 임시로 주입 (build_live_embed 가 읽도록)
        if img_path:
            fname = f"image_{os.path.basename(img_path)}"
            files.append(discord.File(img_path, filename=fname))
            raid["image_url"] = f"attachment://{fname}"
        if thumb_path:
            tname = f"thumb_{os.path.basename(thumb_path)}"
            files.append(discord.File(thumb_path, filename=tname))
            raid["thumbnail_url"] = f"attachment://{tname}"
        embed = await self._build_live_embed(raid)
        try:
            msg = await target_channel.send(embed=embed, files=files or discord.utils.MISSING)
        except discord.HTTPException as exc:
            log.warning("라이브 임베드 발송 실패", exc_info=True)
            await interaction.followup.send(f"임베드 발송 실패: {exc!s}", ephemeral=True)
            return

        # 업로드된 첨부의 CDN URL 을 캐싱(이후 edit 에 재사용)
        cdn_image: Optional[str] = None
        cdn_thumb: Optional[str] = None
        for att in msg.attachments:
            if att.filename.startswith("image_"):
                cdn_image = att.url
            elif att.filename.startswith("thumb_"):
                cdn_thumb = att.url
        if cdn_image or cdn_thumb:
            try:
                await self.db.set_raid_image_urls(
                    raid_id, image_url=cdn_image, thumbnail_url=cdn_thumb,
                )
                log.info(
                    "보스 이미지 CDN 캐시: image=%s thumb=%s",
                    "yes" if cdn_image else "-", "yes" if cdn_thumb else "-",
                )
            except Exception:  # noqa: BLE001
                log.warning("이미지 URL 저장 실패", exc_info=True)

        try:
            await msg.pin(reason="레이드 진행 중")
        except discord.HTTPException:
            pass

        try:
            await self.db.set_raid_message_id(raid_id, msg.id)
        except Exception:  # noqa: BLE001
            log.warning("message_id 저장 실패", exc_info=True)

        # 메모리 캐시 + idle 갱신 타이머 기준점 설정
        self._active_raid_id = raid_id
        self._last_embed_edit = time.monotonic()
        self._embed_dirty = False

        await interaction.followup.send(
            f"{boss.emoji} **{boss.name}** 소환! {target_channel.mention} 에서 진행 중.\n"
            "참가하려면 `/레이드참가` 를 입력하세요.",
            ephemeral=True,
        )

    @app_commands.command(
        name="레이드채널설정",
        description="레이드 전용 채널을 지정하거나 자동 생성합니다.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(채널="레이드 채널 (생략 시 자동 확보/생성)")
    async def set_raid_channel_cmd(
        self,
        interaction: discord.Interaction,
        채널: Optional[discord.TextChannel] = None,
    ) -> None:
        if interaction.guild_id is None or interaction.guild is None:
            await interaction.response.send_message("서버에서만 사용할 수 있어요.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        if 채널 is not None:
            # 명시 지정: 단순 저장
            await self.db.set_raid_channel(interaction.guild_id, 채널.id)
            await interaction.followup.send(
                f"레이드 채널을 {채널.mention} 로 설정했어요.", ephemeral=True,
            )
            return

        # 인자 없음 → ensure-or-create
        ch, err = await self._ensure_raid_channel(interaction.guild)
        if ch is None:
            await interaction.followup.send(err or "확보 실패.", ephemeral=True)
            return
        await interaction.followup.send(
            f"레이드 채널: {ch.mention}\n"
            "(기존 채널을 발견했거나 새로 생성했습니다.)",
            ephemeral=True,
        )

    @app_commands.command(
        name="레이드취소",
        description="진행 중인 레이드를 강제로 취소합니다(오너 전용).",
    )
    async def cancel_raid_cmd(self, interaction: discord.Interaction) -> None:
        if not is_owner(interaction.user.id):
            await interaction.response.send_message("오너 전용이에요.", ephemeral=True)
            return
        raid = await self._get_active()
        if raid is None:
            await interaction.response.send_message("진행 중인 레이드가 없어요.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self._end_raid(int(raid["id"]), "cancelled", final_blow_user_id=None)
        await interaction.followup.send("레이드를 취소했어요.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Raid(bot))
