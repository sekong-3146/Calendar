# DECISIONS

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
