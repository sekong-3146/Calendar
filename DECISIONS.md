# DECISIONS

## 2026-11-09 13:43 할 일 체크 + 미완료 넘기기
- 증상: (신규) 메모 줄을 할 일로 체크하고, 미완료 항목을 다음 날로 넘기고 싶음
- 원인: -
- 처리 (설계·공용 모듈: Claude, 구현: GPT-6 Astra fast 3개 병렬, 통합 테스트: Claude):
  - 사용자 결정:
    - 할 일 문법은 Obsidian과 같은 `- [ ]`
    - 기존 평문 줄은 Ctrl+L이나 우클릭으로 할 일로 바꿈
    - 넘기기는 자동/수동/끄기 중 설정으로 선택
    - 넘긴 원래 줄은 `- [>] 내용 (→ M/D)`로 남김
  - `tasks.py` 신규(순수 함수):
    - 줄 번호는 `body.split("\n")` 기준 0부터. 제목·빈 줄·코드 블록 안은 대상에서 제외
    - `toggle_done`은 줄 내용이 예상과 다르거나 코드 블록 안이면 ValueError
  - `carry.py` 신규:
    - 순서: 오늘 파일에 먼저 쓰고 → 원래 날짜에 `[>]` 표시. 두 번의 쓰기 모두 mtime 충돌 검사
    - 오류가 나면 즉시 멈추고 보고. 다시 실행해도 중복되지 않음(오늘 메모에 같은 문장이 있으면 추가는 건너뛰고 표시만 함)
  - `detail`:
    - Ctrl+L로 평문 → `[ ]` → `[x]` 순환. 여러 줄 선택 가능, Ctrl+Z 한 번에 되돌림
    - 우클릭 메뉴 4종(할 일로 만들기 / 완료 표시 / 완료 해제 / 체크박스 제거)
    - 할 일 줄 끝에서 Enter 치면 `- [ ] ` 이어 붙임
    - `toggle_task_at(line_no, expected_line)` 추가
  - `grid`:
    - 칸에 ☐/☑/→ 표시. ☐ 클릭 → `taskToggled`
    - 지난 날짜 우클릭 → `carryRequested`
    - 날짜가 바뀌면 `dateChanged`
  - `app`:
    - ☐ 클릭 처리: 그 날짜의 확장 창이 열려 있으면 입력창에 반영, 아니면 파일에 바로 씀
    - `_run_carry`: 확장 창에 저장 안 된 편집이 있으면 중단
    - `_auto_carry`: 시작할 때와 자정에 하루 1회(`last_carry`)
    - 메뉴 "미완료 할 일 넘기기"
  - `config`: `carry_mode`(기본 "manual"), `carry_lookback_days`(14), `last_carry` 추가
- 주의:
  - 자동 모드 기본값을 쓰지 않음: 설치하자마자 메모 파일이 바뀌지 않게 하려는 의도
  - 넘기기에서 오늘 파일 쓰기와 원래 날짜 쓰기 순서를 바꾸지 말 것(바꾸면 유실 가능)
  - `[>]`는 Obsidian 기본 테마에서 체크된 것처럼 보일 수 있음(미확인). 그래서 `(→ M/D)`를 붙임
  - 실제 한글 입력기(IME)로 Enter·Ctrl+L을 눌러보는 확인은 안 함(가상 이벤트로만 테스트)

## 2026-11-09 13:07 리뷰 버그 9건 수정 + 툴팁·날짜 이동·메모 열기 기능
- 증상 (코드 리뷰로 발견):
  - 충돌 창을 Esc나 X로 닫으면 편집 중인 메모가 버려짐
  - 메모 읽기 실패 시 입력 감지가 꺼진 채로 남아 이후 입력이 사라짐
  - 인코딩 판별에 실패한 메모를 저장하면 깨진 글자로 원본을 덮어씀
  - 숨겨진 확장 창 / 숨긴 달력이 TOPMOST로 남음
  - 노트 탭 페이지 누수
  - 빈 except, 스캔 오류를 조용히 무시, 레지스트리 오류 시 메뉴가 중간에 끊김
- 원인: 실패 경로 처리 누락. 충돌 창의 Esc가 RejectRole("파일 불러오기") 버튼으로 연결됨
- 처리 (작업: GPT-6 Astra fast, 검토·재테스트: Claude):
  - `detail`:
    - 충돌 창에 "취소" 버튼 추가(Esc/닫기 = 취소, 편집 내용 유지)
    - `_load_memo(day)`는 대상 메모를 먼저 읽고, 성공한 뒤에만 날짜를 바꿈. `blockSignals`는 finally로 해제
    - `show_day`에서 TOPMOST 켜기/끄기를 항상 적용
    - 노트 탭 페이지는 `deleteLater`로 삭제
  - `memofile`: `MemoDoc.error` 추가. 인코딩 오류가 있는 파일은 편집기를 읽기 전용으로 두고, `write`는 OSError로 거부
  - `grid`: 숨겨진 상태라도 raised면 poll 유지(내리기만 함)
  - `mdsource`: `scan_errors` 추가(FileNotFoundError만 무시). 시작할 때와 수동 새로고침 때만 표시
  - `autostart.set_enabled`: 이미 꺼져 있으면 False 반환(빈 except 제거)
  - `app._build_menu`: 레지스트리 읽기 실패 시 항목을 비활성으로 두고 메뉴는 끝까지 만듦
  - 기능:
    - 잘린 칸에 마우스를 올리면 전체 내용 툴팁
    - 확장 창에서 Ctrl+←/→로 날짜 이동(창 위치 유지)
    - "메모 Obsidian에서 열기" 버튼(파일이 없으면 만들지 않고 안내)
- 주의:
  - 충돌 창 "취소" 후에는 `_dirty`가 유지되어 창 닫기와 날짜 이동이 막힘(의도된 동작). 덮어쓰기나 불러오기를 골라야 풀림
  - `show_day(day, anchor=None)`에서 anchor가 None이면 창 위치를 유지함
  - 충돌 검사와 쓰기 사이의 짧은 경쟁 구간은 남아 있음(파일 잠금 없음)
  - `reload_notes`의 stat 예외 처리는 아직 보강하지 않음
  - 실제 Win+D, 실제 Obsidian 열기는 미확인

## 2026-11-09 10:59 달력 헤더에 메뉴(☰) 버튼 추가
- 증상: 설정 메뉴가 트레이 아이콘 우클릭으로만 열림
- 원인: -
- 처리: 헤더 우측(`오늘` 오른쪽)에 `btn_menu`(☰, InstantPopup) 추가. `app.py`에서 트레이와 같은 `QMenu`를 `setMenu`로 연결 → `aboutToShow`로 항목이 매번 다시 만들어짐
- 주의: 메뉴는 트레이와 공유하는 객체 하나. 버튼용 메뉴를 따로 만들면 항목이 어긋날 수 있음

## 2026-11-09 09:12 Win+D에도 달력 유지 + Windows 시작 시 자동 실행
- 증상: Win+D(바탕화면 보기)를 누르면 달력이 바탕화면에 가려짐. 자동 실행 옵션 없음
- 원인:
  - 기존 `check_covered`는 창 중심점 hit test 방식이었는데, 중심점에 투명한 시스템 창(CoreWindow 등)이 겹치면 바탕화면을 감지하지 못함 (11-09 측정에서 Win+D 전부터 CoreWindow가 잡힘)
  - Qt `WindowStaysOnBottomHint`가 WM_WINDOWPOSCHANGING에서 z-order를 강제로 바닥에 둬서, TOPMOST 지정이 무시됨 (실험: hint가 있으면 TOPMOST 실패, 없으면 성공)
- 처리:
  - `winpin.BottomPin.poll()` (0.3초 간격): 포그라운드가 바탕화면(또는 우리 창)이고 z-order상 위에 보이는 Progman/WorkerW가 있으면 → TOPMOST. 포그라운드가 셸이 아닌 다른 앱이 되면 → NOTOPMOST 후 바닥으로
  - `CalendarWindow`에서 `WindowStaysOnBottomHint` 제거 (바닥 고정은 nativeEvent가 담당)
  - 확장 창도 `raisedChanged`에 맞춰 TOPMOST 동기화 (달력 아래로 숨지 않게)
  - `autostart.py` 신규: HKCU Run `MdCal` = `"<venv>\pythonw.exe" "<repo>\MdCal.pyw"`. 트레이 메뉴 체크로 켜고 끔. 앱 시작 시 등록된 경로가 현재 위치와 다르면 갱신
- 주의:
  - `WindowStaysOnBottomHint`를 다시 넣으면 Win+D 대응이 깨짐
  - 실제 Win+D 동작은 확인 못 함 (작업 시점에 화면이 잠겨 있었음). 상태 전환 로직, TOPMOST 플래그, 바닥 고정만 테스트로 확인
  - 자동 실행은 venv 경로에 묶임. 프로젝트 폴더나 venv를 옮기면 앱을 한 번 실행해야 Run 값이 갱신됨

## 2026-11-06 18:29 달력 메모를 md 파일로 저장 + vault 폴더명 표시
- 증상: 메모가 앱 DB에만 있어 Obsidian에서 볼 수 없음. 여러 vault 노트가 섞여 출처 구분 안 됨
- 원인: 1차 요구사항(MD 읽기 전용, 메모는 DB)
- 처리:
  - 사용자 결정: 메모 = `memo_folder`(기본 `C:\Obsidian\해야할거`)의 `YYMMDD.md`, 날짜당 1파일, md가 원본(양방향)
  - `memofile.py` 신규: frontmatter(`date:`) 유지하며 본문만 교체, tmp→replace 원자 저장, 빈 메모는 vault `.trash`로 이동
  - 충돌: 읽을 때 mtime 기억 → 저장 시 다르면 `MemoConflict` → 덮어쓰기/불러오기 선택. 저장 실패 시 창 닫기·날짜 이동·종료 막음
  - Obsidian에서 고친 내용은 감시로 반영(편집 중이 아닐 때만 자동 갱신)
  - 기존 DB 메모는 시작 시 md로 이전(파일 있으면 본문 뒤에 붙임), 성공한 것만 DB에서 삭제
  - `Store.range()` → `colors()`, `MdIndex(folders, memo_folder)`, `DayDetail(memos, index)`로 변경, 호출부 전부 수정
  - 노트 표시에 vault 폴더명 접두어. 구분자는 `/` (맑은 고딕에서 `\`가 `₩`로 보임)
- 주의:
  - `업무` vault는 여전히 읽기 전용. 쓰기는 `memo_folder`의 `YYMMDD.md`만
  - 충돌 검사를 빼면 Obsidian 편집 내용을 덮어씀. 제거 금지

## 2026-11-06 17:46 MdCal 1차 구현 (바탕화면 달력 + MD 연동)
- 증상: (신규) DesktopCal 스타일 바탕화면 달력에 Obsidian/MD 노트를 연동할 필요
- 원인: -
- 처리:
  - Python 3.14 + PySide6-Essentials 6.12.0. `PLAN.md` 기준
  - MD는 파일명 앞 날짜(`YYMMDD`, `YYYYMMDD`, `YYYY-MM-DD`)로 매핑, 읽기 전용. `.`으로 시작하는 폴더 제외
  - 앱 메모·날짜색은 `%APPDATA%\MdCal\mdcal.db`(SQLite)에 저장, 설정은 `config.json`
  - 바닥 고정: `WM_WINDOWPOSCHANGING`에서 `hwndInsertAfter=HWND_BOTTOM` 강제. Win+D 대비로 1초마다 창 중심이 Progman/WorkerW에 가려졌는지 확인 후 HWND_TOP으로 올림
- 주의:
  - MD 파일에 쓰는 코드는 넣지 말 것 (요구사항: 읽기 전용)
  - WorkerW 내부 삽입 방식은 클릭 전달 문제로 의도적으로 안 씀
  - pythonw는 콘솔이 없어서, 예외는 `sys.excepthook`에서 `error.log`에 기록하고 메시지 박스로 표시함. 제거 금지
