# DECISIONS

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
