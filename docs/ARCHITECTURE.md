# 아키텍처 & 기술 메모

조사한 기술 정보와 설계 결정을 기록합니다. (조사일: 2026-05-20)

## 핵심 라이브러리: discord.py 2.7.1

- 최신 안정 버전 **2.7.1** (2026-03 릴리스), Python 3.14 호환 수정 포함.
- **audioop 이슈**: Python 3.13 에서 표준 라이브러리 `audioop` 모듈이 제거됨(PEP 594).
  discord.py 는 `audioop-lts` 패키지를 fallback 의존성으로 가져오므로,
  3.13/3.14 에서도 설치만 하면 자동 해결된다. (이번 설치에서 `audioop-lts 0.2.2` 자동 포함 확인)
- 음악/보이스 기능을 쓰지 않으므로 `discord.py[voice]` extra 는 설치하지 않는다.

## 슬래시 커맨드 모델 (app_commands)

- discord.py 2.0+ 는 `app_commands`(슬래시 커맨드)를 1급으로 지원.
- `commands.Bot` 은 `CommandTree` 인스턴스를 `bot.tree` 로 가진다.
- **동기화(sync)**: 코드상의 커맨드 정의를 디스코드 API에 등록하는 과정.
  - 글로벌 sync 는 전파에 최대 1시간 → 개발/단일 서버엔 부적합.
  - **길드 sync 는 즉시 반영** → 단일 길드 전용 봇에 적합.
- 본 프로젝트는 `setup_hook` 에서:
  ```python
  self.tree.copy_global_to(guild=self.guild_object)  # 글로벌 정의를 길드로 복사
  await self.tree.sync(guild=self.guild_object)       # 길드에만 등록
  ```

## 단일 길드(서버) 전용 보장

두 겹으로 강제한다:

1. **커맨드 노출 제한**: 위 길드 sync 로 지정 서버에서만 커맨드가 보임.
2. **다른 서버 자동 탈퇴**: `on_ready` 에서 `GUILD_ID` 가 아닌 길드에 들어가 있으면 `guild.leave()`.

추가로 `/role` 그룹은 `guild_only=True` 로 DM 사용을 막는다.

## 코그(Cog) 기반 모듈화

- 기능을 `cogs/` 의 독립 모듈로 분리, `setup_hook` 에서 `load_extension` 으로 로드.
- 각 코그는 `async def setup(bot)` 를 노출해야 한다.
- 장점: 기능별 파일 분리, 재시작 없이 reload 가능(`bot.reload_extension`), 에러 격리.

| 코그 | 책임 |
| --- | --- |
| `general` | 핑 등 기본 |
| `voice_log` | 음성/멤버 활동 추적, 로그 채널, 비활성·전체 조회, 통계, 자동 보고 |

> 초기엔 `roles`(역할 관리), `external_api`(날씨) 코그도 있었으나 요구사항 변경으로 제거됨(2026-05-22).

## 명령어 (한국어 전용)

- 모든 슬래시 커맨드는 **한국어 이름만** 제공한다(`/핑`, `/활동확인`, `/스탯`, `/휴면` 등).
  과거 영어 이름(`/ping` 등)은 제거됨. 명령 본문은 공통 `_impl` 메서드 호출 구조 유지.

## 권한 & 인텐트

- **인텐트**: `Intents.default()` + `members=True` + `message_content=True`.
  둘 다 privileged intent → Developer Portal 에서 별도 활성화 필요.
  - `members`: 서버 입·퇴장 추적, `/활동확인`·`/전체확인` 멤버 열거
  - `message_content`: "하루야 …" 메시지 본문을 읽어 AI 대화 처리
- **명령 권한**: `default_permissions` 로 `/로그채널설정`(채널 관리), `/활동확인`·`/전체확인`(서버 관리),
  `/휴면`(역할 관리), `/유저경고`(메시지 관리)를 제한. 디스코드 UI 에서 명령별 권한 추가 조정 가능.

## 비동기/에러 처리

- 모든 명령은 코루틴. 외부/지연 작업 전 `interaction.response.defer()` 로 3초 응답 제한 회피.
- 코그 단위 에러 핸들러(`cog_app_command_error`)로 사용자 친화적 메시지 반환.

## AI 대화 (하루야 트리거)

- 슬래시가 아닌 **`on_message` 리스너**로, 메시지가 `하루야` 로 시작하면 뒷부분을 프롬프트로 사용.
  → 메시지 본문 접근이 필요해 `message_content` 인텐트 필수(@멘션 방식이면 인텐트 없이도 되지만 요구사항은 단어 트리거).
- Google **Gemini**(`gemini-2.5-flash-lite`) 무료 API를 기존 `aiohttp` 로 REST 호출(새 의존성 0).
- `GEMINI_API_KEY` 는 선택값 — 없으면 봇은 정상 동작하고 트리거 시 안내만 출력.
- 답변은 일반 텍스트로 `reply`(2000자 초과 시 분할), 멘션 무음.
- **번역**: `하루야 번역 …` 이면 번역 전용 system 프롬프트로 라우팅(자동 KO↔EN, 언어 키워드로 지정 가능).

## 휴면(비활성) 관리

- `/휴면 표시`: `_collect_inactive` 결과에 `휴면` 역할 부여(없으면 생성), 선택적 DM 경고.
- `/휴면 해제`: 역할 보유자 전체에서 해제.
- **자동 해제**: 휴면 멤버가 음성에 다시 입장하면 `on_voice_state_update` 에서 역할 제거.
- 역할은 이름(`휴면`)으로 조회/생성 → Manage Roles 권한 + 봇 역할이 휴면 역할보다 위 필요.
- DM 일괄 발송은 닫힌 DM·레이트리밋에 대비해 실패를 무시하고 진행(기본 dm=False).

## 데이터 영속 (PostgreSQL / asyncpg)

- **배포 전제**: GitHub 연동 + **Oracle Cloud 평생 무료 VM** 에서 24시간 구동
  (Koyeb 무료 티어는 2026년 Mistral AI 인수로 신규 가입 종료 → VM 방식으로 전환).
- **결정**: 외부 관리형 **PostgreSQL(Neon 무료 티어)** 사용. VM 디스크는 영속적이지만,
  Oracle이 유휴 인스턴스를 회수할 수 있어 데이터를 외부(Neon)에 두면 인스턴스 손실에도 안전.
  (VM에서는 로컬 SQLite도 가능하나 회수 대비상 Neon 유지 권장)
- **드라이버**: `asyncpg` (Python 3.14 cp314 휠 제공, 빌드 불필요). 풀(`create_pool`)로 사용.
- **스키마** (`database.py`, 최초 기동 시 자동 생성):
  - `guild_config(guild_id PK, log_channel_id)` — 로그 채널 ID 영속
  - `voice_activity(guild_id, user_id, last_active, total_seconds, PK(guild_id,user_id))` — 음성 활동
  - `member_log(guild_id, user_id, join_count, leave_count, last_joined_at, last_left_at, PK(guild_id,user_id))`
    — 서버(길드) 입·퇴장 횟수. `record_member_join/leave` 가 `RETURNING` 으로 누적값을 돌려줘 로그에 즉시 표시
- 모든 갱신은 `INSERT ... ON CONFLICT DO UPDATE`(upsert). `last_active` 는 `GREATEST` 로
  과거 값으로 덮이지 않게 한다.

## 음성 활동 추적 설계

- **이벤트**: `on_voice_state_update` (voice_states 인텐트는 기본 포함, 비권한).
  - 입장: 세션 시작 시각 기록 + `last_active` 갱신 + 로그 채널 기록
  - 퇴장: 체류시간 계산해 `total_seconds` 합산 + `last_active` 갱신 + 로그 기록
  - 이동/음소거 등: 활동 유지로 보고 `last_active` 만 갱신
- **하트비트 루프(15분)**: 보이스에 계속 머무는 멤버는 입·퇴장 이벤트가 없어 `last_active`
  가 갱신되지 않는다 → 주기적으로 현재 접속자 `last_active` 를 갱신해 "장기 접속 = 활동"을 반영.
- **기동 스캔(on_ready)**: 봇 재시작 시 이미 보이스에 있는 멤버를 즉시 활동 처리(이벤트 누락 보완).
  VM 재시작/재배포가 잦아도 활동 추적이 끊기지 않는다.

## 서버(길드) 입·퇴장 추적

- **이벤트**: `on_member_join` / `on_member_remove` (members 인텐트 필요, 이미 활성).
  멤버가 서버를 나갔다 다시 들어온 횟수를 `member_log` 에 누적한다(자진 탈퇴·추방 모두 퇴장으로 집계).
- 로그 채널에 `📥 서버 입장 (누적 N회)` / `📤 서버 퇴장 (누적 N회)` 로 즉시 기록.
- **한계**: 추적 시작 이전부터 있던 멤버는 다시 나갔다 들어오기 전까지 기록이 없다(관측된 이벤트만 집계).
  음성 활동(`voice_activity`)과는 별개 테이블로 관리해 개념을 분리.

## 로그 채널 = 가시적 기록 + 안내 채널

- `/setup-log` 가 `@everyone` 비공개 + 봇 쓰기 가능한 채널을 생성(관리자는 권한상 열람 가능).
- 음성 활동이 사람이 읽을 수 있게 이 채널에 기록되고, 비활성 보고도 이곳에 게시된다.
  (실제 질의용 데이터는 Postgres에 있고, 채널은 감사 로그·안내 역할)

## 비활성 안내 & 멘션 처리

- `/inactive [일수]` 와 자동 보고(`tasks.loop`, 기본 168시간)가 동일 로직 사용.
- 대상 = (봇 제외 전체 멤버) 중 `last_active` 가 없거나 기준일 이전인 멤버.
  멤버 캐시는 `guild.chunk()` 로 보강.
- 출력은 `<@user_id>` (= `@username`) 형식으로 **클릭 가능**하되,
  `AllowedMentions.none()` 으로 **실제 알림은 보내지 않는다**(비활성자 무더기 핑 방지).
- 2000자 제한 대응으로 결과를 페이지 단위로 분할 전송.

## 상시 가동

- **현재 방식(VM)**: Oracle VM에서 `systemd`(`deploy/harubot.service`)로 구동.
  `Restart=always` 로 크래시·재부팅 시 자동 복구, 부팅 시 자동 시작. 인바운드 포트 불필요.
- **keepalive(선택)**: 일부 PaaS는 인바운드 HTTP 헬스체크를 요구한다. `keepalive.py` 는
  `PORT` 환경변수가 있을 때만 최소 HTTP 서버(`/`, `/health` → 200)를 띄운다.
  VM/로컬에선 `PORT` 가 없어 자동 비활성 → 코드 분기 없이 어디서나 동작.

## 알려진 제약 / 향후 고려

- 토큰/`DATABASE_URL` 은 `.env`(로컬)·Koyeb Secret(운영)로만 관리, 절대 커밋 금지.
- `total_seconds` 는 봇 재시작 시 진행 중이던 세션 시간을 일부 놓칠 수 있어 **근사치**다.
  비활성 판정의 핵심인 `last_active` 는 입장·이동·하트비트·기동 스캔으로 비교적 정확.
- 활동 로그를 모든 입·퇴장마다 채널에 남기므로, 매우 큰 서버에서는 메시지가 많아질 수 있다
  (필요 시 입장만 기록하도록 축소 가능).
- 명령 수가 늘면 `/role` 처럼 그룹으로 묶어 25개 제한 안에서 관리.
- 향후: AFK 채널 제외 옵션, 비활성 멤버 자동 역할 부여/추방 등 액션 연계 고려.
