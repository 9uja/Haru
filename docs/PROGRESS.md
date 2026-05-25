# 개발 진행 로그

HaruBot 개발 진행 상황을 시간순으로 기록합니다. 최신 항목이 위로 옵니다.

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
