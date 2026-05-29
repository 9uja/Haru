# 보스 이미지 폴더

이 디렉토리에 보스 이미지 파일(`.png`, `.jpg` 등)을 두면, 봇이 첫 `/레이드소환` 시 자동으로 Discord 에 업로드하고 CDN URL 을 캐시합니다.

## 사용법

1. 이미지 파일을 이 폴더에 둡니다. 예:
   ```
   assets/raid/fire_golem.png       (큰 이미지용, 권장 640×360+)
   assets/raid/fire_golem_icon.png  (썸네일용, 권장 256×256)
   ```

2. `raid_config.py` 에서 보스 정의에 파일명만 적습니다:
   ```python
   "fire_golem": BossDef(
       ...
       image_file="fire_golem.png",
       thumbnail_file="fire_golem_icon.png",
   ),
   ```

3. 봇 재시작 → `/레이드소환` 첫 호출 시:
   - 봇이 파일을 Discord 에 업로드
   - CDN URL 을 `raids.image_url` / `raids.thumbnail_url` 컬럼에 저장
   - 이후 라이브 임베드 edit 에 캐시된 URL 만 재사용 (재업로드 없음)

## 보안 메모

- 절대경로(`/etc/...`, `C:\...`)나 상대경로 탈출(`..`) 은 차단됩니다.
- 파일명만 사용하세요 (예: `boss.png`, 하위 폴더는 `subfolder/boss.png` 형태로 가능).
- 존재하지 않는 파일은 경고 로그 후 이미지 없이 진행합니다.

## URL 방식과의 차이

| 방식 | 장점 | 단점 |
|---|---|---|
| `image_url` (외부) | 즉시 사용, 봇 디스크 0 | URL 깨지면 표시 불가 |
| `image_file` (로컬) | 봇 패키지에 포함, 안정적 | 봇 디스크/ZIP 크기 증가 |

둘 다 지정 시 로컬 파일이 우선 적용됩니다.
