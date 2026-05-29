# 개발 진행 로그

HaruBot 개발 진행 상황을 시간순으로 기록합니다. 최신 항목이 위로 옵니다.

---

## 2026-05-30 — 스킬 데미지 공식 config 화 (`DamageFormula`)

- [x] **`raid_core.DamageFormula` dataclass 신규** (14필드, 모두 기본값)
      - 스탯 스케일: `str_scale`/`agi_scale`/`int_scale`/`luk_scale`
      - 치명: `crit_base`/`crit_cap`/`luk_crit_coef`/`crit_base_mult`/`agi_crit_coef`
      - 약점: `weakness_base`/`weakness_cap`/`int_weak_coef`/`weakness_mult`
- [x] **`SkillDef.formula: DamageFormula`** 필드 추가
- [x] **`calc_attack(formula=...)`** 시그니처 확장 — 옛 하드코딩 식 완전 제거
      - 4스탯 가산식: `base × (1 + STR·str + AGI·agi + INT·int + LUK·luk)`
- [x] **`skill_config.py` 확장**
      - 상단 가이드에 "데미지 공식" 섹션(공식·필드·빌드 친화도 설계) 추가
      - **`ATK_FORMULA`** (평타 기본) 노출 — 운영자가 평타도 조정 가능
      - 스킬별 빌드 친화 공식:
        - 💥 강타: `str_scale=0.025` (평타 0.02 보다 ↑)
        - 🏹 화살비: `str_scale=0.012 + agi_scale=0.012` + 치명 보너스
        - 🎯 약점간파: `int_scale=0.035` + `weakness_mult=2.5`
        - ✨ 행운의일격: `luk_scale=0.03` + `crit_base_mult=2.0`
- [x] **빌드 차별화 검증** (Lv 50, 200 stat budget, 2000회 평균):
      - STR 빌드: 평타 42 / 강타 90
      - INT 빌드: 약점간파 **321** (압도)
      - LUK 빌드: 행운의일격 **238** (압도)
      - 균형 빌드: 모두 100~170 사이 (평균 선)
- [x] 호환성: 옛 calc_attack 호출 형태도 안전(formula=None 시 모듈 기본값)

운영자 조정 시나리오:
- 평타 너무 약하다 → `ATK_FORMULA.base_damage=15` 로 변경
- INT 빌드 너무 강하다 → 약점간파 `int_scale=0.02` 로 낮춤
- STR 빌드에 새 스킬 → SkillDef 추가 + `str_scale=0.03` 의 강력 공식

---

## 2026-05-30 — 스킬 해방 시스템 (비밀 조건 + DM 알림)

- [x] **`raid_core.py` 확장**
      - `SkillRequirement` (min_level/min_{str,agi,int,luk}/min_total)
      - `SkillDef` 이동 (cogs/raid.py 에서) + `requirements` 필드 추가
      - `check_requirement(req, level, stats) -> bool` 헬퍼
- [x] **`skill_config.py` 신규** (프로젝트 루트)
      - SKILLS dict 4종(slash/volley/pierce/fortune) — 마나·쿨다운·데미지·**해방 조건** 모두 이곳
      - 상단에 "조건 비밀 정책" 명시 + 밸런스 설계 가이드
      - 현재 조건:
        - 💥 강타: Lv 5 + STR 10
        - 🏹 화살비: Lv 10 + AGI 25
        - 🎯 약점간파: Lv 15 + INT 40
        - ✨ 행운의일격: Lv 20 + LUK 50
- [x] **DB 테이블**: `user_skills (guild_id, user_id, skill_key, learned_at)`
      - PK (guild, user, skill_key) — 중복 학습 방지
      - 메서드: `learn_skill` (멱등), `get_learned_skills` (set 반환)
      - `reset_levels` / `reset_all` 에 user_skills 포함하도록 확장
- [x] **`cogs/raid.py` 통합**
      - SkillDef/SKILLS 정의 제거 → raid_core/skill_config 임포트
      - `RaidPanelView` **동적 버튼 빌드**: 평타는 항상, **학습한 스킬만 버튼 추가**
        - 미해방은 버튼 자체가 존재하지 않음 (비밀 정책)
        - 데코레이터 5고정 → `__init__._build_buttons()` 로 전환
      - `check_skill_unlocks(guild_id, user_id, notify_user?)` 공개 메서드
        - 현재 레벨/스탯 → 모든 잠긴 스킬 검사 → 멱등 학습 → 새 해방분 DM
      - `_dm_new_skills(user, keys)` — 골드 임베드로 1개씩 DM, 차단/실패 silent
      - `/레이드참가`: 해방 체크 → learned 조회 → 패널/뷰에 전달
      - 패널 임베드 "스킬" 섹션도 학습된 것만 표시 (없으면 안내문)
- [x] **자동 해방 트리거**
      - `cogs/leveling.py::_maybe_announce_levelup` — 레벨업 시 Raid 코그의 체크 호출
      - `cogs/stats_rpg.py::allocate_stat` — 분배 직후 호출
      - 안전망: `/레이드참가` 진입 시에도 한 번 더 체크 (놓친 알림 복구)
- [x] **`_make_zip.py`** — INCLUDE 에 `skill_config.py` 추가

비밀 정책 검증:
- 미해방 스킬은 패널 버튼·임베드 어디에도 노출 X
- 슬래시 명령에도 노출 안 됨
- 해방 조건 문구는 어떤 메시지에도 표시되지 않음 (DM 도 "스킬 해방됐다" 만 안내)

다음 단계 후보:
- `/관리 스킬강제해방 <유저> <스킬>` (오너 디버그용)
- 추가 스킬 (5번째 이상 — 코그 dispatch 도 같이 손봐야 함)
- 스킬 트리/전제 조건 (예: 강타 학습 후에만 회오리 해방 가능)

---

## 2026-05-30 — 보스 전용 이미지 (URL + 로컬 파일 자동 업로드)

- [x] **BossDef 에 4필드 추가** (`raid_core.py`)
      - `image_url` / `thumbnail_url` — 외부 URL (즉시 사용)
      - `image_file` / `thumbnail_file` — 로컬 파일명(`assets/raid/` 기준, 자동 업로드)
- [x] **DB 컬럼**: `raids.image_url`, `raids.thumbnail_url` (TEXT)
      - 로컬 파일 첫 업로드 시 받은 CDN URL 을 캐싱 → 이후 edit 에 재사용 (재업로드 0)
- [x] **DB 메서드**: `set_raid_image_urls(raid_id, image_url=?, thumbnail_url=?)`
      - 부분 갱신 지원, `get_raid` SELECT 도 새 컬럼 포함
- [x] **`cogs/raid.py` 통합**
      - `ASSETS_RAID_DIR` 상수 (`assets/raid/` 절대경로)
      - `_resolve_asset_path(filename)` — 디렉토리 탈출/절대경로 차단 보안 검증
      - `summon_raid`: 보스에 `image_file` 있으면 `discord.File` 첨부 + 첫 송신 후 CDN URL 캐싱
      - `_build_live_embed`: `raid.image_url`(캐시) → `boss.image_url`(폴백) 순으로 적용
        - `set_image(url=...)` 큰 이미지 / `set_thumbnail(url=...)` 코너 아이콘 둘 다 지원
- [x] **`assets/raid/` 폴더 + README.md** — 사용법 가이드(권장 해상도·보안·URL vs 파일 비교)
- [x] **`raid_config.py` 가이드 보강**
      - 상단에 "보스 이미지" 섹션 — URL 방식 A) Discord 업로드 → 링크 복사, 로컬 방식 B) 파일 두기
      - 끝의 보스 템플릿 주석에 `image_url/image_file/thumbnail_*` 예시 포함
- [x] **`_make_zip.py` INCLUDE 에 `assets/` 추가** — 배포 ZIP 에 디렉토리/README 포함
- [x] **보안**: 절대경로(`/etc/passwd`)·디렉토리 탈출(`../config.py`) 모두 거부 + 로그
- [x] 검증: 임포트, 경로 안전, ZIP 빌드 모두 통과

사용법 요약:
1) URL 방식 — `image_url="https://..."` (Discord 채널에 업로드 후 우클릭 "링크 복사" 최편함)
2) 파일 방식 — `image_file="boss.png"` + 파일을 `assets/raid/` 에 두기 → 봇이 자동 업로드+캐시

---

## 2026-05-30 — 보스 콘텐츠 모듈 분리 (raid_core.py / raid_config.py)

> 새 보스 추가/조정 시 `cogs/raid.py` 를 건드릴 필요 없도록 분리.

- [x] **`raid_core.py` 신규** (프로젝트 루트)
      - 공용 타입: `PhaseDef`, `DropEntry`, `TraitDef`, `BossDef`
      - 데미지 타입 상수: `DMG_PHYS`/`DMG_ELEM`/`DMG_HOLY`/`DMG_TYPE_EMOJI`
      - 특성 카탈로그: `TRAITS` (이름·이모지·설명만, 동작 분기는 cogs/raid.py)
      - 유틸: `roll_drop(table)` 가중 추첨
      - **순환 임포트 방지**: 어떤 `cogs.*` 도 임포트하지 않음
- [x] **`raid_config.py` 신규** (프로젝트 루트)
      - `BOSSES: dict[str, BossDef]` — fire_golem / ice_wolf / shadow_knight
      - 파일 상단에 **"새 보스 추가 가이드 (4단계)" + 데미지 타입 / 방어 공식 / 특성 / 드롭 아이템 키 표** 문서 주석
      - 끝에 새 보스 **템플릿 예시** 주석 (붙여넣기 → 값만 채우면 됨)
- [x] **`cogs/raid.py` 슬림화** (69 → 61 KB, -8 KB)
      - 타입/BOSSES/_roll_drop 정의 제거
      - `from raid_core import ...` + `from raid_config import BOSSES` 로 대체
      - `_roll_drop` → `roll_drop` 이름 통일 (raid_core 공용)
- [x] **`_make_zip.py` 갱신** — INCLUDE 에 `raid_core.py`, `raid_config.py` 추가
- [x] **검증** — 임포트·BOSSES 식별성·전투 로직(calc_attack/apply_defense)·드롭 추첨 모두 정상

새 보스 추가 절차:
1. `raid_config.py` 의 BOSSES dict 에 새 항목 추가 (템플릿 참고)
2. 봇 재시작 → `/레이드소환` 선택지에 자동 등장
3. 기존 보스 조정도 이 파일에서만 (HP·시간·드롭·방어·특성 등 전부)

---

## 2026-05-30 — 보스 레이드 Phase 3.5 (방어/속성/회피/특성 시스템)

> 동일 보스가 이름·HP만 다르게 느껴지지 않도록 **전투 차별화** 도입.

- [x] **데미지 타입 3종** — 스킬별 자동 분류
      - ⚔️ 물리 (평타·강타·화살비) / 🔮 속성 (약점간파) / ✨ 신성 (행운의일격)
- [x] **방어 시스템** — 보스별 `armor_physical/elemental/holy`
      - 비례 감소 공식: `final = raw × (1 − armor/(armor+100))`
      - 저레벨 무력화 방지(armor 0=영향 없음, armor 100=50% 감소, armor 200=67%)
- [x] **회피 시스템** — 보스별 `evasion (0~1)` 굴림, 빗나가면 데미지 0
- [x] **특성 시스템 6종** (`TRAITS` 딕셔너리)
      - 🛡️ 치명 저항 (`crit_resist`) — 치명타 발동 무효
      - 🕊️ 약점 봉인 (`weakness_resist`) — 약점 공격 발동 무효
      - 🪨 강철 가죽 (`damage_cap`) — 단일 공격 데미지 상한
      - 💚 재생 (`regen`) — 분당 HP 회복 (10초 주기 `regen_loop`)
      - ⚒️ 갑주 균열 (`armor_break`) — HP 일정 % 이하 시 방어 감소
      - 🩸 페이즈 회생 (`phase_heal`) — 페이즈 전환 시 HP 일부 회복
- [x] **보스 3종 차별화**
      - 🔥 fire_golem: P20 E60 H0 / 회피 0% / 특성 없음 — 튜토리얼, **신성 무방비**
      - ❄️ ice_wolf: P40 E25 H35 / 회피 15% / **치명저항** + **재생 80/분** — 회피 짜증, INT 약점간파 효과적
      - 🌑 shadow_knight: P70 E45 H20 / 회피 8% / **강철가죽 400** + **페이즈회생 10%** + **갑주균열(25%↓ 시 ½)** — 신성 약점, 폭딜 무력화
- [x] **`calc_attack` 시그니처 확장** — `boss_traits` 추가 (crit_resist/weakness_resist 검사)
- [x] **`apply_defense(raw, boss, type, hp_pct)`** 헬퍼 — 회피→방어→armor_break→damage_cap 파이프라인
- [x] **DB**: `db.apply_raid_heal(raid_id, amount)` — current_hp += amount (max_hp cap), 반환 (before, after)
- [x] **`regen_loop`** (10초 주기) — active 보스의 regen 특성 검사 후 회복 + 라이브 임베드 dirty 마킹
- [x] **`phase_heal` 통합** — 페이즈 전환 시 자동 회복 + raid_actions 에 로그
- [x] **라이브 임베드 강화**
      - "방어 / 회피" 섹션: 각 타입별 감소율 % 표시 + 회피율
      - "특성" 섹션: 보유 trait emoji/이름/설명 + 핵심 파라미터(cap값/회복량/%)
- [x] **최근 로그**: phase_heal 액션 표시 "🩸 페이즈 회생 +N HP"
- [x] **패널 액션 결과**: 치명/약점/갑주균열/상한/일부회피/완전회피 태그
- [x] **부팅 fingerprint**: "Phase 3.5 (보스 N종, 특성 6종, 데미지타입 3, ...)"

다음 단계 후보(Phase 4):
- `/레이드일정설정 <크론>` — 정기 자동 소환

---

## 2026-05-30 — 보스 레이드 Phase 3 (룰렛 + 결정타 별도 풀 + 드롭 + 신규 보스 2종)

기획서: docs/RAID_PLAN.md §4.2 (등급별 보스), §7.3 (룰렛 연출).

- [x] **`DropEntry` dataclass + 보스별 드롭 테이블**
      - `BossDef.drop_table` (일반 풀), `BossDef.final_blow_table` (결정타 별도)
      - `_roll_drop(table)` — 가중치 기반 1개 추첨, 빈 풀/0 weight 안전
- [x] **분배 규칙 (`_award_drops`)**
      - 승리 시에만 실제 드롭, 데미지 0 = 보상 0 (D13 정책 유지)
      - 순위별 보장 확률: **TOP1 100% / TOP2 60% / TOP3 40% / TOP4+ 20%**
      - **결정타 별도 룰렛 1회 보장** — `final_blow_table` (희귀 가중치↑)
      - 인벤토리 입금: `db.add_to_inventory` (기존 메서드 재사용)
      - 알 수 없는 키는 스킵 + 로그
- [x] **룰렛 연출 (`_send_roulette`) — 3단계 edit (각 1초 간격)**
      - 1단계: "🎰 굴리는 중..."
      - 2단계: "🎰 굴리는 중... ✨ 두근두근 ✨" (색 변경)
      - 3단계: 결과 reveal — 희귀(엘릭서/행운부적) 등장 시 보라색 + ✨ 마크 + 푸터 강조
      - 결정타 룰렛은 마지막 줄에 "💀 결정타 룰렛" 태그
      - **Cloudflare 1015 가드** 활성 시: 룰렛 단축(임베드 1회만, 결과만)
- [x] **신규 보스 2종**
      - ❄️ **얼음 늑대**(엘리트, Lv 45) — HP 8000, 30분, 2페이즈(격노·빙결폭주), base_xp 500/+1500
      - 🌑 **그림자 기사**(레이드, Lv 75) — HP 20000, 60분, 3페이즈(분노·광기·멸세), base_xp 1000/+3000
      - 등급별 드롭 테이블 차등 (희귀 확률 상승)
      - 페이즈 가중치도 등급별 차등 (보스가 받는 데미지)
- [x] **`BossDef.tier`** ("일반"/"엘리트"/"레이드") — 결과 임베드 푸터에 노출
- [x] **결과 임베드 푸터 강화** — 등급/레벨업 수/룰렛 안내 한 줄 구성
- [x] README/PROGRESS 갱신

확률 표 요약 (각 풀의 아이템 % — 가중치 기반):

| 보스 | small | large | elixir | charm |
|---|---:|---:|---:|---:|
| fire_golem 일반 | 56.8 | 28.4 | 11.4 | **3.4** |
| ice_wolf 엘리트 | 34.1 | 34.1 | 22.7 | **9.1** |
| shadow_knight 레이드 | 16.7 | 27.8 | 33.3 | **22.2** |
| fire_golem 결정타 | 21.1 | 31.6 | 31.6 | **15.8** |
| ice_wolf 결정타 | 15.0 | 25.0 | 35.0 | **25.0** |
| shadow_knight 결정타 | 10.0 | 20.0 | 35.0 | **35.0** |

다음 단계 후보(Phase 4):
- 정기 cron 자동 소환 (`/레이드일정설정`)

---

## 2026-05-30 — 보스 레이드: 전용 채널 자동 생성 + 채널 게이트

기획서 §3 "채널·권한 구조" 완성 (Phase 4 의 채널 부분만 선반영).

- [x] **자동 채널 생성**: `_ensure_raid_channel(guild)` 헬퍼
      - 순서: 저장된 ID 확인 → 이름(`보스레이드`)으로 발견 → 봇이 직접 생성
      - 권한 부족(`manage_channels` 없음) 시 친절한 안내 메시지
      - 생성 후 자동으로 `raid_channel_id` 동기화
      - 채널 topic 자동 설정: 사용법 안내문
- [x] **`/레이드소환` 동작 변경**: 현재 채널 폴백 제거 → **항상 전용 채널 사용**(없으면 자동 생성)
- [x] **`/레이드참가` 채널 게이트**: 라이브 임베드가 있는 채널(`raid.channel_id`)에서만 동작
      - 다른 채널에서 호출 시 "`<#raid_ch>` 채널에서만" 안내(silent mention)
- [x] **`/레이드채널설정` 확장**
      - 채널 인자 지정 시: 단순 저장 (기존 동작 유지)
      - 인자 생략 시: **ensure-or-create** (저장 → 이름 검색 → 생성)
      - 자동 생성 결과(기존 발견인지 새로 만든 건지) 안내
- [x] 상수: `RAID_CHANNEL_NAME = "보스레이드"`, `RAID_CHANNEL_TOPIC = "..."`

영향:
- 운영자는 `/레이드채널설정` 한 번이면 끝 (또는 첫 `/레이드소환` 시 자동)
- 라이브 임베드가 #일반 같은 곳에 박힐 일 없음
- 채널 외에서 `/레이드참가` 시도해도 다른 채널 오염 없이 안내만 ephemeral

남은 Phase 4 항목 (별도 진행):
- 정기 cron 스케줄(`/레이드일정설정`)

---

## 2026-05-30 — 보스 레이드 Phase 2 (스킬·마나·페이즈)

기획서: docs/RAID_PLAN.md (v0.2) §5.3~5.5.

- [x] **스킬 4종 도입** (기획서 §5.4)
      - `SkillDef` dataclass: key, name, emoji, mana_cost, cooldown, multiplier, hits, force_crit, force_weakness
      - 💥 강타 (30MP/60s/×1.8) — STR 폭딜
      - 🏹 화살비 (50MP/90s/×0.6×3히트) — 분산 데미지
      - 🎯 약점간파 (60MP/120s/×2.5+약점확정) — INT 시너지
      - ✨ 행운의일격 (40MP/100s/×2.0+치명확정) — LUK 시너지
      - 스킬 쿨다운에도 AGI 보정 적용(최대 75% 감소)
- [x] **마나 시스템** (기획서 §5.3)
      - 최대 마나 = `100 + INT` (Lv25 풀 INT = 200MP, Lv50 풀 INT = 300MP)
      - 자연재생 = `(5 + INT × 0.1) / 분` (Lv50 풀 INT = 10/분)
      - 인메모리 추적 (`_mana`, `_mana_last_update`): 첫 진입 = 풀마나, 호출 시점에 재생 계산
      - 레이드 종료 시 자동 비움
- [x] **페이즈 전환** (기획서 §5.5, §4.3)
      - `PhaseDef` dataclass: hp_pct, name, color, damage_taken_mult, flavor
      - `fire_golem` 페이즈: 격노(HP 60%↓, 받는 ×1.2) → 파괴(HP 30%↓, 받는 ×1.5)
      - HP 임계 통과 시 `_next_phase` 가 감지 → `db.set_raid_phase` 갱신 + raid_actions 에 `phase:격노` 로그
      - 라이브 임베드 제목·색이 페이즈 따라 변경 (`_phase_color`)
- [x] **`calc_attack` 시그니처 확장**
      - 키워드 인자 multiplier / force_crit / force_weakness / phase_mult
      - 평타·스킬·페이즈 효과 모두 동일 함수로 처리
- [x] **`RaidPanelView` 5버튼 + 동적 비활성화**
      - row 0: 평타 / row 1: 강타·화살비·약점간파·행운의일격
      - `refresh_button_states(atk_cd_left, mana, skill_cds_left)` 로 갱신 — 쿨다운/마나 부족 시 disabled
      - 모든 버튼이 `handle_action(action_key)` 단일 dispatch
- [x] **개인 패널 임베드 확장**
      - 마나바(현재/최대) + 평타 쿨다운 상태 + 스킬 4종 상태 표시 (사용가능/마나부족/쿨다운 N초)
      - 액션 후 결과는 author 영역에 `{스킬} −데미지 (치명!, 약점!) → 남은 HP X` 한 줄
- [x] **자원 부족 시 UX**
      - 쿨다운/마나 부족이면 패널만 갱신해서 사유를 author 에 한 줄로 표시 (별도 ephemeral 안 띄움)
- [x] **DB 변경**
      - `db.set_raid_phase(raid_id, phase)` 추가 (단순 UPDATE)
- [x] **`fire_golem` 밸런스 재조정**
      - HP 1,500 → 2,500 (스킬 도입으로 DPS↑ 보정)
- [x] README/PROGRESS 갱신

다음 단계 후보(Phase 3):
- 보상 룰렛 임베드 연출 (3회 edit, 슬롯 머신 식)
- 보스 추가(엘리트·레이드)
- 아이템 드롭 테이블 (보상)
- 결정타 시 별도 룰렛 1회 (현재는 보너스 XP 만)

---

## 2026-05-30 — 보스 레이드 Phase 1 MVP

기획서: `docs/RAID_PLAN.md` (v0.2) — D1~D13 확정 후 구현 진입.

- [x] **DB**
      - `raids` 테이블 + 길드당 active 1개 unique partial index (`uniq_active_raid_per_guild`)
      - `raid_participants` (PK: raid_id+user_id) + ON DELETE CASCADE
      - `raid_actions` (BIGSERIAL id, 라이브 임베드 "최근 5턴" 표시용) + `idx_raid_actions_recent`
      - `guild_config.raid_channel_id` 컬럼
      - 14개 DB 메서드: create_raid / get_active_raid / get_raid / set_raid_message_id /
        apply_raid_damage(원자적 HP 차감 + last_action_at) / end_raid(트랜잭션, final_blow 마킹) /
        join_raid / add_participant_damage(upsert) / get_raid_participants / get_raid_top_n /
        count_raid_participants / log_raid_action / recent_raid_actions / set·get_raid_channel
      - `reset_all` 도 raids 포함하도록 확장 (CASCADE 로 자동 연쇄 삭제)
- [x] **`cogs/raid.py` 신규**
      - 보스 정의 코드 상수(D10): `BOSSES["fire_golem"]` — Lv25, HP 50k, 30분 제한, 보상 500/2000
      - 데미지 공식 §5.1 구현: `calc_attack(stats, phase_mult)` — 힘/민첩치명/지능약점/행운치명확률
      - 쿨다운 §5.2: `calc_cooldown(agi_pt)` — 60s base, AGI 보정, 15s 캡
      - **개인 ephemeral 패널** (D7) — `RaidPanelView` (View):
        - 본인만 동작 (`interaction.user.id` 검증)
        - 평타 버튼만 (스킬은 Phase 2)
        - 14분 timeout (Discord ephemeral 15분 한계 직전)
      - **라이브 임베드 갱신 루프** (D11): 2초 디바운스 + 3초 하드 캡 + `GUARD.is_paused()` 존중
      - **시간 초과 감시 루프**: 10초 주기, 시간 한계 도달 시 `_end_raid('defeat')`
      - **종료 처리**: 비례 보상(`0.5x + 3x × 비율`) + 결정타 +2000 (D6) + 패배 위로 `0.2x` (D9)
      - `_raid_lock`(asyncio.Lock) 으로 데미지 적용·HP 차감 직렬화 → 동시 결정타 race 방지
- [x] **슬래시 4개**
      - `/레이드참가` (누구나) — 패널 호출
      - `/레이드소환 <보스>` (manage_guild) — DB 생성 → 라이브 임베드 발송 → pin → message_id 저장
      - `/레이드채널설정 [채널]` (manage_guild) — 라이브 임베드 채널 지정
      - `/레이드취소` (오너 전용) — 강제 종료
- [x] **정책 통합**
      - 오너 무시 모드 유저는 평타 불가(정책 일관성)
      - D2 확정 반영: 보스 공격 없음, 유저 페널티 0
      - D13 확정 반영: AFK 시스템 미구현 — 데미지 0 = 보상 0 자연 차단
- [x] `bot.py` INITIAL_COGS 에 `cogs.raid` 추가
- [x] README 표·프로젝트 구조 갱신

---

## 2026-05-29 — XP 페이스 재조정 (메시지 1·음성 60/5분)

- [x] **메시지 XP**: 15~25 랜덤 → **1 고정**. 60초 쿨다운 유지(도배 방지).
- [x] **음성 XP**: 분당 10(`tasks.loop(minutes=1)`) → **5분마다 60**(`tasks.loop(minutes=5)`).
      시간당 720 XP (이전 600 대비 +20%). 큰 청크로 적립 → DB 호출 횟수 1/5 로 감소.
- [x] 상수 정리: `MSG_XP_MIN/MAX/VOICE_XP_PER_MIN` 제거 → `MSG_XP=1, VOICE_TICK=5min, VOICE_XP_PER_TICK=60`.
- [x] 부팅 fingerprint 로그·`/레벨` 임베드 푸터를 새 페이스 표기로 갱신.
- [x] 부스트 / 지능 / 행운 보너스는 동일하게 곱연산(메시지·음성 모두).
- [x] README/PROGRESS 동기화.

---

## 2026-05-29 — RPG 스탯 시스템(힘/민첩/지능/행운) + 레벨 손실 시 LIFO 환불

- [x] **DB**
      - `user_stats(guild_id, user_id, str_pt, agi_pt, int_pt, luk_pt)` — 누적 분배
      - `stat_allocations(id BIGSERIAL, guild_id, user_id, stat, count, created_at)` — 이벤트 단위 LIFO 이력
      - `idx_alloc_user (guild_id, user_id, id DESC)` 인덱스 — LIFO 조회 O(log)
      - 메서드: `get_user_stats`, `allocate_stat`(트랜잭션), `refund_stat_points`(LIFO 차감/삭제 트랜잭션),
        `reset_user_stats`, `set_user_xp`, `subtract_xp`. `reset_levels`/`reset_all` 도 동기 확장.
- [x] **`cogs/stats_rpg.py` 신규**
      - `STATS_META` 4종(💪힘/💨민첩/🧠지능/🍀행운) + 한국명·이모지·설명
      - `unspent_points(level, stats)` = `level*4 − sum(stats)`
      - 슬래시 3개:
        - `/능력치 [멤버]` — 임베드: Lv/미분배/누적분배 + 스탯 4개 필드 + **현재 효과 요약**
        - `/능력치분배 <스탯> <포인트>` — `Range[int, 1, 999*4]`, 미분배 보유 검증, 1행 이력 추가
        - `/능력치리셋 확인:True` — 명시 확인 시에만 전체 환수
- [x] **`cogs/leveling.py` 통합**
      - `_get_int_xp_bonus()` — 메시지·음성 XP 적립에 (1 + INT × 0.005) 곱연산
      - `_get_luk_drop_bonus()` — 드롭 굴림에 (1 + LUK × 0.01) 곱연산, 0.95 캡(완전 100% 방지)
      - **`lose_xp(guild_id, user_id, amount)` 공개 API** — XP 차감 → 레벨 비교 →
        하락 시 `(levels_lost × 4)` 포인트를 LIFO 환불. 결과 dict 반환.
        도박/감소 등 향후 기능은 이 한 줄만 호출하면 안전 작동(다중 레벨 손실 지원).
- [x] **오너 검증 명령** `/관리 xp감소 <유저> <양>` (admin.py)
      - `Leveling.lose_xp` 호출 → 결과(전후 XP·레벨·환불 내역) ephemeral 임베드 출력
      - 도박 코그 없이도 환불 메커니즘 단위 테스트 가능
- [x] `bot.py` INITIAL_COGS 에 `cogs.stats_rpg` 추가
- [x] README 표·새 섹션·프로젝트 구조 갱신

---

## 2026-05-29 — 레벨링 확장: 곡선 메이플화 + 옵션 토글 + 아이템 드롭 + XP 부스트

- [x] **XP 곡선**: 기존 MEE6 식 → 메이플스토리 영감 다항식 `xp_to_next(L) = 0.5L² + 5L + 10`
      - L1=15 (메이플 클래식 동일), L100=5,010, L500=127,510, **L999=502,510**
      - 누적 합도 BIGINT 안전, 최대 레벨 999 고정(`MAX_LEVEL`)
- [x] **소스별 토글 + 알림 채널 설정** — `guild_config` 에 3컬럼 추가
      - `level_msg_xp_enabled BOOLEAN` (기본 TRUE)
      - `level_voice_xp_enabled BOOLEAN` (기본 TRUE)
      - `level_up_channel_id BIGINT NULL` (NULL=트리거 채널)
      - `db.get_level_config` / `db.set_level_config` (부분 갱신·채널 해제 지원)
- [x] **아이템 드롭** — 메시지 XP 적립 시 2% 가중치 추첨
      - 4종(`ITEMS` dict 상수): 🍙 작은비상식량(1.5x/30분, w=50), 🍱 큰비상식량(2x/20분, w=25),
        🧪 엘릭서(3x/10분, w=10), 🍀 행운의부적(5x/5분, w=3)
      - `user_inventory(guild_id, user_id, item_key, qty)` PK 3컬럼
      - 드롭 시 채널에 1회 안내(채널 mention OK, role/everyone 차단)
- [x] **활성 부스트(XP 배율)** — 유저당 1개, 최신 사용이 덮어씀
      - `active_boosts(guild_id, user_id PRIMARY KEY, multiplier REAL, expires_at TIMESTAMPTZ)`
      - `set_active_boost`/`get_active_boost`(만료된 행 자동 제외)/`clear_expired_boosts`
      - XP 적립 직전 multiplier 적용(메시지·음성 둘 다), 미보유/만료 시 1.0
      - 시간당 1회 만료 부스트 정리 루프(`boost_cleanup_loop`)
- [x] **새 슬래시 3개**
      - `/인벤토리`(누구나·ephemeral): 보유 아이템 + 활성 부스트 표시
      - `/사용 <아이템>`(누구나, Choice 4종): 1개 소비 → 부스트 등록(기존 덮어쓰기)
      - `/레벨설정` (서버관리, manage_guild): 메시지xp/음성xp/알림채널/알림채널해제 옵션, 결과 임베드
- [x] `/레벨` 임베드에 활성 부스트 표시, 만렙 도달 시 "🌟 만렙 달성" 필드
- [x] `db.reset_levels` 가 user_xp + user_inventory + active_boosts 모두 비움, `reset_all` 도 동기 갱신
- [x] **오너 무시 모드** 유저는 메시지·음성 XP·드롭 모두 차단(정책 일관성)
- [x] Cloudflare 1015 가드 존중 — 발송 단계만 스킵, DB 누적은 계속
- [x] README 표·기능 섹션 갱신

---

## 2026-05-29 — 레벨링 시스템 추가

- [x] `database.py`: `user_xp(guild_id, user_id, xp, last_msg_xp_at)` 테이블 + 인덱스(`idx_user_xp_rank` ON `(guild_id, xp DESC)`)
- [x] DB 메서드: `add_message_xp` / `add_voice_xp` / `get_msg_xp_cooldown` / `get_user_xp` / `get_user_rank` / `top_xp` / `reset_levels`. `reset_all` 도 `user_xp` 포함하도록 확장.
- [x] `cogs/leveling.py` 신규
      - **메시지 XP**: 15~25 랜덤, 유저당 60초 쿨다운(메모리+DB 2단계), 봇·`!?/.` 시작·길이<2 제외
      - **음성 XP**: 분당 10. `tasks.loop(minutes=1)` 가 모든 음성 채널을 순회하며 **비봇 2명 이상** + **AFK/청각차단 아님** 인 유저에게만 지급
      - 레벨 곡선: MEE6 식 `5L²+50L+100` (`level_from_xp` 누적 합 산정, 저장 X)
      - **레벨업 알림**: 메시지로 오른 경우에만 같은 채널에 한 줄 🎉. 음성 진행은 조용
      - **Cloudflare 1015 가드** 존중: 차단 중엔 알림만 스킵, 누적은 계속
      - **오너 무시 모드** 유저는 XP 적립도 차단(정책 일관성)
      - 부팅 fingerprint 로그: `Leveling cog 로드 — 메시지 XP 15~25/60s, 음성 XP 10/min (peers≥2)`
- [x] 슬래시: `/레벨 [멤버]`(임베드: 레벨/누적XP/순위/진행률바), `/랭킹`(TOP 10, 🥇🥈🥉)
- [x] `bot.py` INITIAL_COGS 에 `cogs.leveling` 추가
- [x] README 표·기능 섹션·프로젝트 구조 갱신

---

## 2026-05-28 — 채널 메시지 일괄 삭제 `/청소`

- [x] `cogs/moderation.py` 에 `/청소 <개수>` 추가
      - `app_commands.Range[int, 1, 100]` 입력 검증 (Discord bulk_delete 한도)
      - `manage_messages` 기본 권한 + 봇의 채널 권한(`manage_messages` · `read_message_history`) 사전 확인
      - **고정 메시지(pinned) 는 보존** (`check=lambda m: not m.pinned`)
      - **Cloudflare 1015 가드** 통합: 차단 중엔 호출 자체 차단(`GUARD.is_paused()`)
      - 14일 초과/고정 등으로 일부 스킵되면 결과에 별도 표기, audit log reason 에 실행자 기록
      - 응답은 ephemeral 로 채널 오염 없음
- [x] README 표·기능 갱신

---

## 2026-05-27 — Cloudflare 1015 자동 회피 가드(`http_guard`) 추가

- 배경: Wispbyte 공유 노드 IP 가 Cloudflare 1015 로 차단됐을 때, 봇이 계속 송신을
  시도하면 차단이 더 길어진다. 로그인 자체도 막혀 부팅 크래시 → Wispbyte 자동
  재시작이 또 다른 1015 를 유발하는 악순환을 봤다. 그래서 봇이 **스스로 침묵**해
  카운터가 식도록 한다.
- [x] `http_guard.py` 신규: `HttpGuard` 싱글톤(`GUARD`) + `install_http_hook(bot)`
      - `bot.http.request` 를 감싸 응답 본문에 `error code: 1015` 마커가 보이면
        `GUARD.trip()` → 기본 **1시간** 동안 `is_paused()` True
      - 이미 트립돼 있으면 더 늦은 만료 시각으로만 갱신(중복 트립 안전)
- [x] `bot.py`:
      - `HaruBot.__init__` 마지막에 `install_http_hook(self)` — 로그인 전에 후킹
      - `main()` 에서 `bot.run()` 의 `HTTPException` 을 잡고 1015 면 큰 경고와 함께
        `sys.exit(2)` — Wispbyte 의 60초 재시도 차단 패턴을 유도해 더 두드리지 않게
- [x] Fire-and-forget 송신 게이트 (가드 활성화 동안엔 스킵):
      - `cogs/voice_log.py::_send_log` (음성·멤버 로그 — 가장 잦은 송신)
      - `cogs/bump.py::bump_loop` (예약 시각은 비우지 않아 가드 해제 후 다음 사이클에 발송)
      - `cogs/bump.py::on_message` 의 ✅ 반응
      - `cogs/ai_chat.py::on_message` 진입 직후 (트리거·임의답장·자연어 관리명령 전부)
      - `cogs/welcome.py::on_member_join` (입장 인사)
      - `cogs/fun.py::on_message` (랜덤 이모지 반응)
- [x] 슬래시 명령은 게이트하지 않음 — 어차피 1015 면 응답 실패가 정상 동작이고,
      사용자가 명시적으로 호출한 케이스라 차단 카운터에 미치는 영향이 작다.
- [x] 검증: `HttpGuard.looks_like_1015` 마커 매칭(긍정/부정/None) 통과,
      `trip()` 후 `is_paused()` True 확인, 전체 모듈 임포트 정상

---

## 2026-05-27 — AI 챗 트리거를 **봇 닉네임 기반 동적 계산**으로 전환

- [x] 모듈 상단의 `TRIGGER="하루야"` / `SYSTEM_HINT`(='하루') / `RANDOM_SYSTEM`(='하루') 하드코딩 제거
- [x] `AIChat._vocative_suffix(name)` 추가: 한국어 호격조사 자동 — 받침 있으면 `아`('준'→'준아'), 없으면 `야`('하루'→'하루야'), 한글 외 끝글자는 `야`('Luna'→'Luna야')
- [x] `_bot_name(guild)` 추가: 길드 닉네임 우선 → 사용자명 → 폴백(`하루`). 매 메시지마다 호출하므로 봇 이름 변경에 **즉시 반응**(재배포·코드 수정 불필요).
- [x] `_trigger(guild)` = `_bot_name + _vocative_suffix` — `on_message` 에서 메시지별로 계산해 `startswith` 매칭
- [x] `SYSTEM_HINT_TEMPLATE` / `RANDOM_SYSTEM_TEMPLATE` 도입 — `_handle_trigger` / `_maybe_random_reply` 에서 현재 이름으로 포맷
- [x] `chat_history` 저장 시 봇 발화의 role 라벨도 현재 봇 이름으로 저장(예전 '하루' 라벨은 그대로 보존)
- [x] `_build_request(prompt, bot_name)` 시그니처 변경 — 페르소나 system 에 봇 이름 주입
- [x] `_handle_trigger(message, content, trigger)` 시그니처 변경 — 호출부에서 트리거를 넘김(닉변 시 일관성↑)
- [x] 검증: 호격조사 8가지 케이스(`하루/준/루나/철수/진/현/Luna/''`) 전부 통과, 모듈 임포트 정상
- [x] README/모듈 docstring 갱신: `<봇이름><야|아>` 표기로 통일, 동적 트리거 안내 추가

---

## 2026-05-26 — 봇 오너(최고 등급) 전용 기능 추가

- [x] `owner.py` 신규: `OWNER_ID=379934490866352130` (닉네임 `구자`), `is_owner()` 헬퍼.
      서버 관리자 역할과 별개로 **고정 ID 한 명**만 정책 명령 사용 가능.
- [x] `/관리` 슬래시 그룹(`cogs/admin.py` 신규):
      - `db초기화 <chat|memory|voice|warnings|all>` — 위험하므로 60초 버튼 확정(`ConfirmResetView`)
      - `무시 <유저>` / `무시해제 <유저>` — AI가 그 유저 메시지를 완전히 무시
      - `지시 <유저> <지시>` / `지시해제 <유저>` — 그 유저와의 대화에 추가 system 지시 주입
      - `목록` — 적용된 오버라이드 보기
- [x] `database.py`:
      - `user_overrides(guild_id,user_id,mode,instruction)` 테이블 추가 — `mode='ignore'|'instruct'`
      - `set/clear/get/list_user_override`, 영역별 리셋(`reset_chat_history/memory/voice/warnings/all`)
- [x] `cogs/ai_chat.py` 통합:
      - **오버라이드 게이트**: `on_message` 진입 시 `ignore` 모드면 AI가 트리거·임의답장·기억 모두 차단. 오너 본인은 안전장치로 절대 차단되지 않음.
      - **지시 주입**: chat 모드 system 프롬프트에 `[오너 지시 — <유저> 대상]` 섹션 추가
      - **자연어 라우팅**: 오너의 `하루야 …` 메시지에서 멘션/디스코드 ID/닉네임 + (`무시`/`차단`/`무시 해제`/`에게는 …`/`지시 해제`) 패턴을 정규식으로 라우팅
      - 자연어 `DB 초기화`는 안전을 위해 슬래시 확정 흐름으로 안내
- [x] `bot.py` INITIAL_COGS 에 `cogs.admin` 추가
- [x] README 표·새 섹션·프로젝트 구조 갱신

---

## 2026-05-26 — 범프 알림 구독을 DB → **전용 역할** 방식으로 전환

- [x] `/범프알림` 을 역할 토글로 변경: 누르면 `범프알림` 역할 부여/해제(자동 생성, mentionable=True, 골드)
- [x] 리마인더 발송 시 **역할 1개만 멘션** — 개별 유저 80명 배치 전송 제거, 메시지가 깔끔하고 처리도 간단
- [x] 임베드 "💡 알림 받기" 필드를 역할 기반 설명으로 갱신
- [x] DB 정리: `bump_subscribers` 테이블/`toggle_bump_subscriber`·`list_bump_subscribers` 메서드 제거
- [x] 권한 안내: 봇에 **역할 관리** 권한 필요, 봇 역할이 `범프알림` 역할보다 **위**여야 함(Forbidden 처리)
- [x] 패턴: 기존 `휴면` 역할 자동 생성 로직(`voice_log.py`)을 그대로 따름
- [x] README의 표·범프 섹션 갱신, 컴파일 OK

---

## 2026-05-25 — 범프 알림 구독(/범프알림) + 멘션 + 임베드 안내

- [x] `/범프알림`(누구나): 본인 구독 토글(`bump_subscribers` 테이블, `toggle_bump_subscriber`)
- [x] 범프 리마인더 발송 시 구독자 **@멘션(핑)** — 80명씩 나눠 전송, 구독자 없으면 무음
- [x] 알림 본문을 **임베드**로 전환(제목/설명/푸터/타임스탬프) + **`/범프알림` 설명 필드** 포함
- [x] 멘션은 메시지 본문(content)에 두어 핑이 가도록, 임베드는 안내용
- [x] 검증: 최상위 17개, `범프알림` 등록 확인, 컴파일 OK

---

## 2026-05-24 — 로그를 임베드로 정리

- [x] 로그 채널 메시지(음성 입·퇴장, 서버 입·퇴장)를 평문 → **임베드**로 전환(`_log` → `_send_log`)
- [x] 멤버 입장 임베드: 아바타·계정 생성일(절대+상대)·누적 입장 + **신규 계정(7일 미만) 경고**
- [x] 멤버 퇴장 임베드: 아바타·누적 퇴장·함께한 기간
- [x] 음성 입·퇴장은 컴팩트 임베드(설명+타임스탬프+푸터)
- [x] **모든 로그 임베드에 해당 유저 스탯 포함**(서버 입·퇴장 / 누적 음성 / 최근 활동 / 경고) — `_add_stats`
      통합 1쿼리 `get_member_stats` 로 잦은 음성 로그의 DB 부하 최소화
- [x] 검증: 잔재 없음, 컴파일·로드 OK(16개 명령)

---

## 2026-05-24 — 서버/개인 기억 분리 + 채널·유저 맥락 + DB 보호 정리

- [x] **기억 분리**: 서버 지식(`knowledge`, 관리자 `/기억추가`) ↔ **개인 기억**(`user_memory`, 누구나 `하루야 … 기억해`/`/내기억추가`)
      → 답변 시 서버 지식 + 말한 사람의 개인 기억을 함께 주입
- [x] 자연어 `기억해` 를 개인 기억으로 변경(권한 게이트 제거), `/내기억목록`·`/내기억삭제` 추가
- [x] **대화 맥락 채널별+유저별**: `chat_history` 에 `user_id` 추가, `get_context`가 `channel_id=C OR user_id=U` 로 합쳐 최근 N개
- [x] **DB 보호 정리**: 매 저장마다 prune 제거 → 유지보수 루프(1시간)가 `chat_history` 행 수 > `CHAT_HISTORY_MAX_ROWS`(기본 50만) 일 때만 오래된 순으로 90%까지 정리
- [x] `cogs/maintenance.py` 추가, 검증: 최상위 16개·유지보수 로드

---

## 2026-05-24 — 자연어 "기억해" → DB 영구 저장

- 질문: "기억해" 같은 명령으로 DB 영구 저장 가능? → 가능(`/기억추가`가 이미 영구 저장 중)
- [x] 자연어 트리거 추가: `하루야 <내용> 기억해`(또는 `기억해 <내용>`, 외워둬 등) → `knowledge` 테이블 영구 저장
- [x] 안전장치: **서버 관리 권한자만** 등록(지식은 모든 답변에 반영되므로 오염/악용 방지), AI 키 없어도 동작
- [x] 검증: 접두/접미 추출 정상, 일반 문장은 None 처리

---

## 2026-05-24 — 대화 맥락 영속화 + 기억량 확대

- 요청: 대화 맥락을 더 많이, 재시작에도 유지
- [x] 메모리(deque) → **DB `chat_history` 테이블**로 전환 → 재시작에도 유지
- [x] 기억 턴 수 기본 3 → **8**, `CHAT_HISTORY_TURNS` 로 조절
- [x] `add_chat_turns`(executemany 저장 + 채널당 최근 N개만 prune, 한 커넥션), `get_chat_history`(최근 N, 오래된 순)
- [x] 조회/저장 실패해도 대화는 계속 진행(예외 무시)
- [x] 검증: 최상위 13개, history_turns=8 로드 확인

---

## 2026-05-24 — AI "기억": 지식(FAQ) + 대화 맥락

- 질문: 자체 학습 가능? → **모델 파인튜닝/훈련은 무료 여건상 불가**(GPU·메모리·비용, 모델도 외부 API라 변경 불가).
  대신 **기억을 프롬프트에 주입**하는 방식으로 "발전하는 것처럼" 구현(리소스·대역폭 부담 거의 0).
- [x] 지식: DB `knowledge` 테이블 + `/기억추가`·`/기억목록`·`/기억삭제`(서버 관리). 답변 시 최근 지식을 system 에 주입(최대 1500자)
- [x] 대화 맥락: 채널별 최근 3턴을 **메모리**(deque)에 보관 → "하루야" 대화에 주입(DB 부하 0, 재시작 시 초기화)
- [x] `_build_request` 가 모드(chat/translate) 반환 → chat 에만 지식·맥락 적용(번역/임의답장 제외)
- [x] 검증: 최상위 13개, 지식 컨텍스트·맥락 기억 동작 확인

---

## 2026-05-24 — 범프 감지 진단/보강

- 증상: 범프 알림이 작동 안 함 → 원인 후보 (채널 미설정 / 성공 문구 마커 불일치)
- [x] DISBOARD 메시지 수신 시 **INFO 진단 로그**(성공감지·채널설정 여부·실제 텍스트) 추가
- [x] 임베드 전체(제목/본문/푸터/필드) 텍스트로 매칭(`_disboard_text`), 마커 보강(끌어올/범프했/thumbsup 등)
- [x] 검증: EN/KO 성공 감지 True, 쿨다운 False
- [x] **실제 한국어 DISBOARD 문구 확인** → "서버 갱신 완료!" → 마커 `갱신 완료`/`갱신완료` 추가로 감지 해결

---

## 2026-05-24 — DB 재시도 로그 소음↓ + 범프 루프 DB폴링 제거

- 증상: `DB 일시 오류, 재시도 1/3:`(메시지 빈칸) WARNING이 ~15분 간격 반복 — 2번째 시도에서 자동 복구(데이터 손실 없음)
- 원인: Neon 무료 유휴 연결 정리/콜드 스타트 + **범프 루프가 매 1분 DB 조회**해 부하·끊김 유발
- [x] `database._run`: 자동복구 재시도 로그 WARNING→**DEBUG**, `%r`로 예외 타입 표기(빈 메시지 해소)
- [x] 범프: 예약 시각을 **메모리 캐시**로 보관 → 루프는 메모리만 확인(매분 DB 조회 제거). 시작 시 1회 로드, 감지/발송 때만 DB 기록
- [x] DB: `get_bump_channel`/`get_due_bump_reminder` → `get_bump_state` 로 정리
- 효과: 반복 WARNING 사라짐, 불필요한 DB 호출/Neon 상시기동 제거(무료 컴퓨트 절약)

---

## 2026-05-23 — 임의 채팅 AI 답장 (낮은 확률)

- [x] `ai_chat` 에 `_maybe_random_reply`: "하루야" 트리거가 아닌 일반 메시지에 **확률(기본 2%, `REPLY_CHANCE`)** 로 가볍게 답장
- [x] 쿼터 보호: **전역 쿨다운 60초** + 백엔드 일시정지(`_available`) 존중 + 실패는 조용히 무시
- [x] 짧은 응답용 파라미터(`GEN_RANDOM` temp 0.8/200), 친구 톤 `RANDOM_SYSTEM`
- [x] `on_message` 를 `_handle_trigger`/`_maybe_random_reply` 로 분리
- [x] 검증: 확률 1→답장 / 쿨다운 차단 / 확률 0→스킵

---

## 2026-05-23 — 범프 알림 채널을 명령어로 지정

- [x] `/범프채널설정 [채널]`(서버 관리) 추가 — 리마인더 보낼 채널 지정
- [x] 알림은 **감지된 채널이 아니라 지정 채널**로 발송. 채널 미지정이면 감지해도 예약 안 함
- [x] DB: `set_bump_reminder` → `set_bump_channel`/`get_bump_channel`/`schedule_bump_reminder` 로 분리
      (채널은 명령어로, 예약 시각은 감지로 갱신 — 서로 안 덮음)
- [x] 검증: 컴파일 + 최상위 10개(범프채널설정 포함, 전부 한국어)

---

## 2026-05-23 — DISBOARD 범프 리마인더

- 요청: "2시간마다 DISBOARD `/bump` 자동 입력" → **봇이 타 봇 슬래시 명령 실행 불가(Discord 제약), 셀프봇은 ToS 위반**이라 불가함을 안내
- 대안 채택(사용자 선택): **정밀형 + 멘션 없이** — DISBOARD(302050872383242240) 범프 성공 감지 후 2시간 뒤 같은 채널에 알림
- [x] `cogs/bump.py`: `on_message` 로 DISBOARD 성공(👍/done/올렸 등) 감지 → `bump_reminder` 에 예약(now+2h), ✅ 반응
- [x] 1분 주기 루프가 예약 도래 시 알림 발송(멘션 없음) 후 비움. DB 저장이라 **재시작에도 유지**
- [x] DB `bump_reminder`(guild_id, channel_id, remind_at) + set/get_due/clear
- [x] 검증: 코그 로드, "Bump done" 감지 / "Please wait" 무시 확인

---

## 2026-05-23 — 번역 품질/반복 폭주 수정 (특히 Groq 폴백)

- 증상: Gemini 한도 초과로 Groq 폴백 시 일본어 번역이 한국어로 같은 말 무한 반복(LLM 루프)
- [x] 모드별 생성 파라미터 분리: 대화 `temp 0.7/800`, **번역 `temp 0.2/400`**(저온도+짧은 상한으로 반복 폭주 차단)
- [x] 일본어 프롬프트를 **예시 기반**으로 재작성(꺾쇠 `<>` 제거 → 모델이 형식 따라가기 쉽게)
- [x] `_build_request` → (prompt, system, gen) 3-튜플, `_ask`/`_call_gemini`/`_call_groq` 가 gen 사용
- [x] 검증: 번역 모드 파라미터·예시 포함·gen 백엔드 전달 확인

---

## 2026-05-23 — AI 백엔드 Gemini + Groq 폴백

- [x] `_ask` 를 **Gemini 우선 → 429/실패 시 Groq 폴백** 구조로 변경(백엔드별 일시정지 `_gemini_pause`/`_groq_pause`)
- [x] `_call_gemini`/`_call_groq` 분리, Groq 는 OpenAI 호환(`/openai/v1/chat/completions`) 기존 aiohttp 호출(새 의존성 0)
- [x] config/.env 에 `GROQ_API_KEY`·`GROQ_MODEL`(기본 llama-3.3-70b-versatile) 추가
- [x] 키가 하나도 없을 때만 미설정 안내, 둘 다 일시정지면 "지금은 잠시 쉴래요."
- [x] 검증: 모의로 ①Gemini429→Groq 폴백 ②정지중 Groq 직행 ③둘다 초과→예외 확인
- 비고: 대화 방식(트리거·번역·발음·쿨다운)은 백엔드 무관하게 동일, 품질만 모델별 차이 가능

---

## 2026-05-23 — AI 한도 초과(429) 반복 호출/로그 폭주 방지

- 증상: 무료 한도 초과 중에도 매 호출마다 API 재시도 → 429 반복 + traceback 로그 폭주
- [x] 429 응답을 `QuotaError(retry_after)` 로 구분, 에러 메시지의 `retry in Xs` 를 파싱
- [x] 한도 초과 시 `retry_after` 동안 **봇 자체 일시정지**(`_paused_until`) → 그 동안은 API 호출 없이 바로 "지금은 잠시 쉴래요."
- [x] 한도 초과는 traceback 없이 한 줄(`AI 무료 한도 초과 — 약 N초 일시중지`)로만 기록
- 효과: 반복 429·로그 폭주 중단, 한도 더 깎지 않음

---

## 2026-05-23 — 상호작용 만료(10062) 수정: defer 누락 보강

- 증상: `/스탯` 등에서 `404 Unknown interaction(10062)` — DB 콜드 스타트로 3초 내 미응답 → 토큰 만료
- [x] **DB 조회 전에 `defer()` 추가**: `/스탯`(공개), `/로그채널설정`·`/환영채널설정`(ephemeral) → 이후 `followup.send`
- [x] `cog_app_command_error` 를 try/except 로 감싸 죽은 상호작용에 대한 2차 예외 방지
- 비고: `/활동확인`·`/전체확인`·`/휴면`·`/유저경고` 는 이미 defer 선행이라 영향 없음, `/채널안내`·`/핑` 은 DB 미사용

---

## 2026-05-23 — 영어 명령어 전체 제거 (한국어 전용)

- [x] 모든 슬래시 명령의 영어 이름 제거, **한국어 이름만 유지**
      (`핑`, `로그채널설정`, `활동확인`, `전체확인`, `스탯`, `휴면 표시·해제`, `채널안내`, `환영채널설정`, `유저경고`)
- [x] 영어 그룹 `/dormant` 제거(한국어 `/휴면` 유지), 각 영어 래퍼 명령 삭제(공통 `_impl` 유지)
- [x] "하루야" 메시지 트리거는 영어 명령이 아니므로 유지
- [x] 검증: 스모크 = 최상위 **9개**, 영어 이름 명령 **0개** 확인
- [x] 문서(README·SETUP·DEPLOY·ARCHITECTURE) 영어 명령 표기 정리, 동기화 수 9로 갱신

---

## 2026-05-23 — 유저 경고 시스템

- [x] `cogs/moderation.py`: `/유저경고 @유저 <내용>`(영어 `/warn`) — 경고 추가(메시지 관리 권한)
- [x] DB `warnings` 테이블(id, guild_id, user_id, moderator_id, reason, created_at) + 인덱스, 메서드 `add_warning/get_warning_count/get_warnings`(추가는 5회 재시도)
- [x] `/스탯`(stats) 에 **경고 횟수 + 최근 경고 5건** 표시
- [x] 봇/본인 경고 방지, 사유 500자 제한, 경고 시 대상 멘션
- [x] 검증: 컴파일 + 스모크(최상위 18개, warn/유저경고 등록)
- 참고: /스탯은 공개라 경고 내역도 공개로 보임(원하면 모더만 보이게 분리 가능)

---

## 2026-05-23 — /activity·/inactive 공개 응답으로 변경

- [x] 두 명령을 ephemeral(본인만) → **공개 응답**으로 변경(`defer()`/`followup.send` 에서 ephemeral 제거)
- 버튼(◀▶·🔃)은 여전히 명령 실행자만 조작(다른 사람은 보기만) — `interaction_check` 유지

---

## 2026-05-23 — 일본어 번역 시 한글 발음 표기

- [x] `하루야 번역 일본어 …` 일 때 번역문 + **한글 발음**을 함께 출력하도록 전용 프롬프트 분기(`_build_request`)
- [x] 출력 형식: `<일본어 번역>` 줄 + `(<한글 발음>)` 줄
- [x] 검증: `_build_request` 라우팅(일본어/자동/지정/일반) 확인

---

## 2026-05-22 — DB 간헐 끊김 데이터 누락 점검 & 보강

- 점검: 쓰기 경로별 누락 위험 분석
  - `touch_active`(자가복구)·`add_session`(근사치)·`get_*`(보존) → 위험 낮음
  - **`record_member_join/leave`(1회성)** = 누락 시 영구 손실 → 최우선 보강 대상
  - **loop(heartbeat/report)** = DB 예외로 영구 중단 위험
- [x] 재시도 강화: 기본 2→**3회**, 백오프 최대 8초 캡, `_execute/_fetch*` 에 `retries` 인자
- [x] 입·퇴장 기록은 **5회 재시도**(콜드 스타트 더 길게 견딤)
- [x] `heartbeat_loop`/`report_loop` 를 try/except 로 보호 → 일시 장애에도 다음 주기 계속 실행
- [x] 검증: 모의 풀로 3회/5회 재시도 복구·소진 확인
- 잔여 한계: Neon 이 재시도 창(수~수십 초) 내내 다운인 그 순간의 입·퇴장 1건은 손실 가능(매우 드묾). 0%가 필요하면 로컬 아웃박스 큐 도입 가능.

---

## 2026-05-22 — 온보딩: 신규 입장 랜덤 환영 + 채널 안내

- [x] `cogs/welcome.py` 신규
- [x] **신규 입장 환영**: `on_member_join` 시 환영 채널(미설정 시 `guild.system_channel`)에 랜덤 인사(7종) + 채널 안내 힌트, 새 멤버 멘션(핑)
- [x] `/환영채널설정 [채널]`(영어 `/welcome-channel`): 환영 채널 지정 → `guild_config.welcome_channel_id`(컬럼 추가, ALTER 포함)
- [x] `/채널안내`(영어 `/channels`): 카테고리별 공개 텍스트 채널 + 토픽 임베드 안내
- [x] 검증: 컴파일 + 스모크(최상위 16개, on_member_join 리스너 2개)

---

## 2026-05-22 — DB 연결 회복력 강화 (Neon 콜드 스타트 대응)

- [x] 증상: `on_voice_state_update` 에서 asyncpg 연결 TLS 타임아웃(Neon 무료 티어 autosuspend 후 콜드 스타트)
- [x] `database.py` 전면 보강: 모든 쿼리를 `_run` 재시도 래퍼 경유(일시 오류 시 지수 백오프 2회 재시도)
      - 재시도 대상: `OSError`(TimeoutError 포함)·`InterfaceError`·`PostgresConnectionError`
- [x] 풀 설정: `min_size=0`(유휴 시 Neon 일시정지 허용=무료 컴퓨트 절약), `max_inactive_connection_lifetime=30`(끊긴 연결 방지), `command_timeout=15`
- [x] 검증: 모의 풀로 2회 실패→복구 / 3회 실패→예외 확인
- 권장: 잦으면 `DATABASE_URL` 을 Neon `-pooler` 엔드포인트로

---

## 2026-05-22 — 랜덤 이모지 반응 (재미 기능)

- [x] `cogs/fun.py`: 메시지마다 일정 확률로 랜덤 이모지 반응. 기본 5%(`REACT_CHANCE` 로 0~1 조절, 0=off)
- [x] AI·DB 미사용(순수 로컬 난수 + 반응 API) → **리소스 거의 없음**, 무료 한도와 무관
- [x] 검증: 컴파일 + 스모크(react_chance 0.05, on_message 리스너 2개)
- 참고: 봇에 'Add Reactions' 권한 필요(보통 기본 허용)

---

## 2026-05-22 — AI 사용자별 쿨다운 + 실패 응답 통일

- [x] **사용자별 쿨다운**: 1인당 `AI_COOLDOWN_SECONDS`(기본 10초)에 1회. 초과 시 메시지 대신 **⏳ 반응**만(도배 방지)
- [x] 호출 실패(429/quota 등) 시 원문 에러 대신 **"지금은 잠시 쉴래요."** 로 통일(상세는 봇 로그에만)
- 원인: 무료 티어 RPM/RPD 한도 초과(코드 문제 아님) → 쿨다운으로 호출 폭주 예방

---

## 2026-05-22 — 비활성 자동 관리(휴면 역할) + AI 번역

### 비활성 자동 관리
- [x] `/휴면 표시 [일수] [dm]`(영어 `/dormant set`): 비활성 멤버에게 `휴면` 역할 부여(없으면 생성), 선택 DM 경고
- [x] `/휴면 해제`(`/dormant clear`): 휴면 역할 일괄 해제
- [x] **자동 해제**: 휴면 멤버가 음성 재입장하면 `on_voice_state_update` 에서 역할 제거
- [x] 권한 안내 갱신: 봇에 **Manage Roles 다시 필요**(휴면 역할 부여), 봇 역할이 휴면보다 위
- [x] voice_log 에 `cog_app_command_error` 추가(권한/Forbidden 친화적 메시지)

### AI 번역
- [x] `하루야 번역 <문장>` → 자동 KO↔EN, `하루야 번역 일본어 …` 로 대상 언어 지정
- [x] 번역 전용 system 프롬프트로 라우팅(`_build_request`), 결과만 출력

- [x] 검증: 컴파일 + 스모크 = 최상위 **12개**(휴면/dormant 그룹 포함), 충돌 없음
- 참고: DM 일괄 발송은 기본 off(dm=True 시), 닫힌 DM/레이트리밋 대비 실패 무시

---

## 2026-05-22 — AI 대화를 "하루야" 메시지 트리거로 변경

- [x] `/ai`·`/대화` 슬래시 제거 → **`on_message` 리스너**로 `하루야 <메시지>` 형태 처리
- [x] `bot.py` 에 `message_content` 인텐트 활성화(메시지 본문 읽기) — **포털에서도 MESSAGE CONTENT INTENT 켜야 함**
- [x] 답변은 일반 텍스트 reply(2000자 초과 시 분할), 타이핑 표시, 멘션 무음
- [x] 문서 갱신: README·SETUP(인텐트 2종 안내)·ARCHITECTURE
- [x] 검증: 컴파일 + 스모크(message_content=True, on_message 리스너 등록, 슬래시 10개)
- 참고: 이제 봇 기동에 **SERVER MEMBERS + MESSAGE CONTENT** 두 특권 인텐트가 필요

---

## 2026-05-22 — AI 대화 기능 추가 (/ai·/대화, Google Gemini)

- [x] 무료 LLM API 조사 → **Google Gemini(AI Studio)** 채택(카드 불필요, 하루 1,000회+, 한국어 우수)
- [x] `cogs/ai_chat.py` 신규: `/ai <메시지>`·`/대화` — Gemini `gemini-2.5-flash-lite` REST 호출(기존 aiohttp 사용, 새 의존성 0)
- [x] `config.py` 에 선택적 `GEMINI_API_KEY` 추가 → 키 없으면 봇은 정상 동작, 명령만 비활성 안내
- [x] 답변은 임베드로 표시(질문=author, 답변=description), 멘션 무음
- [x] 슬래시 커맨드만 사용 → MESSAGE CONTENT 인텐트 불필요
- [x] `.env.example`·README·SETUP 갱신, 검증: 컴파일 + 스모크(최상위 **12개**, `ai`·`대화` 포함)

---

## 2026-05-22 — 명령어 정리(제거) + 한국어 이름 변경

- [x] **제거**: `/role add·remove`(+한국어), `/weather`(+한국어), `/info`(+한국어)
      → `cogs/roles.py`, `cogs/external_api.py` 삭제, `bot.py` INITIAL_COGS 정리
- [x] **한국어 이름 변경**: `로그설정→로그채널설정`, `비활성→활동확인`, `활동→전체확인`, `통계→스탯`
- [x] 남은 명령: `ping/핑`, `setup-log/로그채널설정`, `inactive/활동확인`, `activity/전체확인`, `stats/스탯`
- [x] 문서 갱신: README(표·구조), SETUP(권한에서 Manage Roles 제거), ARCHITECTURE(코그표 등)
- [x] 검증: 컴파일 + 스모크 = 최상위 **10개**(영어 5 + 한국어 5)
- 참고: 역할 명령 제거로 봇 초대 시 **Manage Roles 권한 불필요**(Manage Channels 만 필요)

---

## 2026-05-22 — 명령어 한국어 이름 추가 (영어/한국어 병행)

- [x] 모든 슬래시 커맨드에 한국어 이름 별칭 추가(기능 동일): `핑/정보/날씨/역할(추가·제거)/로그설정/비활성/활동/통계`
- [x] 중복 코드 없이 영어·한국어 명령이 공통 `_impl` 메서드를 호출하도록 리팩터링
      (general·external_api·roles·voice_log 전부), 한국어 그룹 `/역할`(추가/제거) 별도 Group
- [x] discord.py가 한글 커맨드 이름 허용함을 사전 검증
- [x] 검증: 컴파일 + 스모크 = **최상위 16개**(영어 8 + 한국어 8), 이름 충돌 없음

---

## 2026-05-22 — /activity·/inactive 인터랙티브 임베드(버튼 페이지·정렬)

- [x] `views.py` 신규: `MemberListView`(discord.ui.View) — 임베드 + 버튼
      **◀️ 이전 / ▶️ 다음 / 🔃 정렬 변경**(비활성순·활동순·이름순), 페이지당 10명, 180초 타임아웃
- [x] 명령 실행자만 버튼 조작(`interaction_check`), 타임아웃 시 버튼 비활성화
- [x] 임베드 멘션은 클릭 가능하되 알림(핑) 없음 → 별도 AllowedMentions 불필요
- [x] `/inactive`, `/activity` 를 평문 페이지 → **인터랙티브 임베드**로 교체
- [x] 자동 보고(report_loop)는 비대화형 `build_static_embed`(상위 30명) 임베드로 게시
- [x] `/stats` 는 `views.days_ago` 재사용, 중복 헬퍼(`_paginate`/`_line_*`/`_days_ago`) 제거
- [x] 검증: 컴파일 + 스모크(커맨드 10개, 23명→3페이지, 정렬 3종 동작 확인)
- 참고: 이 기능도 SERVER MEMBERS INTENT 활성화 후에야 봇이 기동됨(미적용 시 PrivilegedIntentsRequired)

---

## 2026-05-21 — 카드 불필요 무료 호스팅 대응 (Wispbyte)

### 배경
- 봇 용량 측정: 코드/문서 **63.7KB(18파일, 755 LOC)**, 의존성 **~27MB**, RAM ~100MB 내외 → 무료 저사양 OK.
- 요구: **신용카드 없이** 24시간 무료 호스팅 → Oracle(카드 필요) 대신 **Wispbyte**(Pterodactyl 패널, 카드 불필요) 채택.
  DB는 Neon(카드 불필요) 유지.

### 작업 (저사양 패널 맞춤)
- [x] `bot.py`: `chunk_guilds_at_startup=False` — 시작 시 전체 멤버 캐시 미수신(필요 시 on-demand chunk)으로 RAM 절약
- [x] `database.py`: 연결 풀 `max_size` 5→3 (저RAM + Neon 무료 연결 한도 고려)
- [x] `start.sh` 추가 — 패널 Startup Command 용(`pip install -r requirements.txt && python bot.py`)
- [x] `.gitattributes` — `*.sh` LF 고정(리눅스 패널 CRLF 오류 방지)
- [x] `docs/DEPLOY.md` 재구성 — A. Wispbyte(카드 X) / B. Oracle VM(카드 O), Neon 공통 단계
- [x] 검증: 컴파일 + 스모크 테스트(커맨드 10개, `chunk_guilds_at_startup=False` 적용 확인)

---

## 2026-05-21 — 서버 입·퇴장 횟수 기록 + /stats

> 처음엔 "음성 채널" 입·퇴장 횟수로 구현했으나, 요구사항은 **서버(길드)를 나갔다 다시
> 들어온 횟수**임을 확인하고 정정함.

- [x] DB: 음성 카운트 롤백(`voice_activity` 는 last_active/total_seconds 로 원복),
      별도 **`member_log`** 테이블 추가(`join_count, leave_count, last_joined_at, last_left_at`)
- [x] DB 메서드: `record_member_join`/`record_member_leave`(`RETURNING` 으로 누적값 반환),
      `get_member_log`, `get_voice_stats`
- [x] 이벤트: `on_member_join`/`on_member_remove` 로 서버 입·퇴장 감지(추방 포함) →
      로그 채널에 `📥 서버 입장 (누적 N회)` / `📤 서버 퇴장 (누적 N회)` 기록
- [x] `/stats [멤버]`: 서버 입·퇴장 횟수 + 누적 음성 체류시간 + 최근 활동을 임베드로 표시(생략 시 본인)
- [x] 검증: 컴파일 + 스모크 테스트로 커맨드 10개 + 리스너 4종
      (`on_member_join/remove`, `on_voice_state_update`, `on_ready`) 등록 확인
- [x] 문서: README·ARCHITECTURE·PROGRESS 갱신
- 한계: 추적 시작 전부터 있던 멤버는 재입장 전까지 기록 없음(관측 이벤트만 집계)

---

## 2026-05-20 — 무료 호스팅 전환 (Koyeb → Oracle Cloud VM)

### 배경
- **Koyeb 무료 티어가 2026년 Mistral AI 인수로 신규 가입 종료** → 무료 24시간 호스팅 대안 필요.
- 조사 결과 가장 견고한 무료 옵션은 **Oracle Cloud Always Free VM**(ARM Ampere A1, 영구 무료).

### 결정 & 작업
- [x] 호스팅: **Oracle Cloud 무료 VM + systemd** 로 전환 (사용자 선택)
- [x] `deploy/harubot.service` 추가 — `Restart=always`, 부팅 자동 시작, 인바운드 포트 불필요
- [x] 데이터: **Neon Postgres 유지** — VM 회수(약 60일 유휴 시) 대비 외부 보존
- [x] `keepalive.py` 는 그대로 유지(= `PORT` 없으면 자동 비활성, VM에선 불필요)
- [x] 문서 갱신: `docs/DEPLOY.md` 전면 재작성(Oracle 가이드), README·ARCHITECTURE의 Koyeb 언급 정리

### 다음 할 일 (TODO)
- [ ] Oracle 무료 VM 생성(Ubuntu 24.04, Ampere A1) → git clone → venv 설치
- [ ] `.env` 작성(토큰·GUILD_ID·Neon DATABASE_URL) 후 수동 기동 확인
- [ ] `harubot.service` 등록 → `systemctl enable --now` 로 24시간 구동
- [ ] `/setup-log`, `/inactive`, `/activity` 실동작 확인

---

## 2026-05-20 — 전체 멤버 활동 조회(/activity) 추가

- [x] `/activity` 명령 추가: 전체 멤버를 `@username — 최근 활동: N일 전` 형식으로 조회
      (봇 제외, 가장 오래 비활성 순 정렬, 무음 멘션, 페이지 분할, 관리자 권한)
- [x] `_paginate` 를 라인 포맷터 주입형으로 일반화 → `/inactive`(마지막 활동 날짜)와
      `/activity`(N일 전) 가 동일 로직 공유
- [x] 검증: 컴파일 + 스모크 테스트로 커맨드 9개 등록 확인
- 결과: `/inactive` 는 "정리 대상 보고", `/activity` 는 "전체 현황 조회" 로 역할 구분

---

## 2026-05-20 — 음성 활동 추적 & 비활성 관리 시스템

### 요구사항
1. 봇 전용 로그 채널 개설  2. 로그 채널이 음성 활동을 기록(DB 역할)
3. 한 달+ 비활성 유저 리스트업으로 관리자 안내  4. `@username` 형식으로 바로 상호작용

### 의사결정
- **배포**: GitHub + Koyeb(24시간) 예정. Koyeb 로컬 디스크가 휘발성이라 SQLite/JSON 파일은
  재배포 시 소실 → **외부 PostgreSQL(Neon 무료)** 로 결정. (Koyeb 무료 PG는 활성 5시간 제한)
- **안내 방식**: 명령어 + 자동 정기 보고 둘 다.
- **드라이버**: `asyncpg 0.31.0` (Python 3.14 휠 제공 확인).

### 완료
- [x] `database.py` — asyncpg 풀, 스키마 자동 생성, 활동 upsert/조회 메서드
- [x] `config.py` — `DATABASE_URL`, `INACTIVE_DAYS`, `REPORT_INTERVAL_HOURS` 추가
- [x] `cogs/voice_log.py`
  - `on_voice_state_update` 입·퇴장 추적(체류시간 누적) + 로그 채널 기록
  - 15분 하트비트 + on_ready 기동 스캔으로 장기 접속/재시작 누락 보완
  - `/setup-log` 봇 전용(비공개) 채널 생성 + 채널 ID 영속
  - `/inactive [일수]` + 주기 자동 보고, `@멘션`(클릭 가능·무음) + 페이지 분할
- [x] `keepalive.py` — `PORT` 있을 때만 헬스체크 HTTP 서버(Koyeb 상시 가동)
- [x] `bot.py` — DB/헬스서버 수명관리, voice_log 코그 등록
- [x] `Dockerfile`, `.dockerignore` — 컨테이너 배포(Python 3.12-slim)
- [x] 검증: 컴파일 + 오프라인 스모크 테스트로 커맨드 8개 등록 확인
      (`inactive, info, ping, role(+add/remove), setup-log, weather`)
- [x] 문서: README/SETUP/ARCHITECTURE 갱신 + **docs/DEPLOY.md**(Koyeb+Neon) 신규

### 다음 할 일 (TODO)
- [ ] Neon DB 생성 + `.env` 작성 후 실제 토큰으로 라이브 기동 테스트
- [ ] 서버에서 `/setup-log` → 음성 입·퇴장 기록 및 `/inactive` 실동작 확인
- [ ] GitHub push → Koyeb Web Service 배포 → 24시간 가동 확인
- [ ] (선택) 비활성 멤버 자동 역할/추방 등 후속 액션, AFK 채널 제외 옵션

---

## 2026-05-20 — 프로젝트 초기 구축

### 완료
- [x] **기술 조사**: discord.py 최신 버전(2.7.1)·Python 3.14 호환성·audioop 이슈 확인
      → 결과는 [ARCHITECTURE.md](ARCHITECTURE.md) 에 정리
- [x] **개발 환경**: Python 3.14.3 + venv, `discord.py 2.7.1` / `python-dotenv 1.2.2` /
      `aiohttp 3.13.5` 설치 및 import 검증 (`audioop-lts 0.2.2` 자동 포함)
- [x] **스캐폴딩**: `bot.py`, `config.py`, `cogs/`(general·roles·external_api),
      `requirements.txt`, `.env.example`, `.gitignore`
- [x] **단일 길드 전용**: `setup_hook` 길드 sync + `on_ready` 비허용 서버 자동 탈퇴
- [x] **기능 구현**
  - `/ping`, `/info` (general)
  - `/role add`, `/role remove` — 권한·역할 위계 검증 포함 (roles)
  - `/weather <도시>` — Open-Meteo 연동, 키 불필요 (external_api)
- [x] **검증**: 오프라인 스모크 테스트로 6개 커맨드 트리 등록 확인
      (`info, ping, role, role add, role remove, weather`)
- [x] **문서화**: README, SETUP, ARCHITECTURE, PROGRESS

### 환경/버전 스냅샷
| 항목 | 버전 |
| --- | --- |
| Python | 3.14.3 |
| discord.py | 2.7.1 |
| python-dotenv | 1.2.2 |
| aiohttp | 3.13.5 |

### 다음 할 일 (TODO)
- [ ] 실제 봇 토큰으로 라이브 기동 테스트 (현재는 오프라인 검증까지만 완료)
- [ ] `.env` 작성 후 대상 서버에 봇 초대 → 슬래시 커맨드 실동작 확인
- [ ] (선택) 환영 메시지/자동 역할 등 멤버 관리 기능 확장
- [ ] (선택) 로깅을 파일로 저장, 운영 배포 방식 결정

---

## 사용법 메모
이 파일은 작업 단위가 끝날 때마다 갱신합니다. 새 작업 시작 시 위에 날짜 섹션을 추가하고,
완료 항목은 `[x]`, 남은 항목은 `[ ]` 로 표시하세요.
