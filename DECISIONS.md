# DECISIONS

## 2026-11-10 21:41 자체 리뷰 + Astra 잔여분 7건 수정
- 증상:
  1. 메모 저장 중 파일이 잠겨 있으면 `261110.md.tmp`가 폴더에 남고 바로 실패 창 (이 PC에서 테스트가 5회 중 1회 같은 오류로 실패)
  2. `config.json`에 타입이 틀린 값이 있어도 그대로 받아들임 → 그리기/타이머에서 예외 → 오류 창이 계속 쌓임
  3. 상세창이 열린 날짜는 칸 체크박스가 코드블록 안의 줄도 바꿈 (Astra B7)
  4. 설정한 MD 폴더가 지워졌다 다시 생겨도 달력에 안 돌아옴, 알림 없음 (Astra B2)
  5. 파일 하나의 stat이 실패하면 그 노트가 달력에서 빠짐 (Astra B5)
  6. 목록 항목 안의 코드블록(`- ```` 포함) 뒤 할 일이 안 보이고, 추가/이월 시 불필요한 ``` 가 파일에 들어감
  7. 테스트가 고정 시간 대기에 의존, 가끔 실패
- 원인:
  1. `os.replace` 실패 시 정리·재시도 없음
  2. `load_config`가 키 이름만 확인, `_excepthook`에 재진입·반복 방지 없음
  3. `toggle_task_at`만 `editable_mask`를 안 봄
  4. 사라진 루트는 감시할 대상이 없는데 되살아났는지 확인하는 경로가 없음
  5. `_scan_dir`의 파일별 예외 처리가 노트를 버림
  6. `_scan_fences`가 목록 항목 범위를 모름 (파일 끝까지 코드로 봄)
  7. `pump(초)` 고정 대기
- 처리:
  1. `memofile.replace_retry`(잠금 오류만 0.05초 간격 3회), 실패 시 `.tmp` 삭제
  2. `config._valid`: 타입이 틀린 키는 기본값 + 시작 시 경고. `_excepthook`: 같은 오류는 한 번만 표시, 로그는 5회까지, 창이 떠 있는 동안 새 창 안 띄움
  3. `toggle_task_at`에 `editable_mask` 검사 추가
  4. `MdIndex._revive` 타이머(5초, `missing`이 있을 때만 동작) + `_check_missing`/`_roots_lost`. 시작 때 없던 폴더가 나중에 생겨도 읽음. 사라질 때 트레이 알림 1회
  5. stat 실패한 파일은 캐시의 이전 노트를 유지
  6. `_scan_fences` 재작성: 목록 항목의 내용 열을 스택으로 추적, 항목이 끝나면 그 안의 코드블록도 끝남. 목록 안에서 열린 채 끝난 코드블록에는 닫는 줄을 추가하지 않음
  7. `tests.wait_until(조건)`으로 교체 (전체 약 10초 → 5초), 테스트의 파일 교체도 `replace_retry`
- 주의:
  - 펜스 규칙은 Qt 파서(md4c)와 무작위 문서 2만 건 비교로 맞춤. 남은 차이는 "빈 `- ```` 뒤에 들여쓰기 없는 ```` ``` py````"뿐 (Qt 쪽 특이 동작으로 판단). **Obsidian 실제 동작은 확인 못 함**
  - 비교 스크립트는 저장소에 없음. 펜스 규칙을 다시 바꾸면 `test_append_result_renders_as_a_checkbox_not_code`에 사례를 추가해 확인할 것
  - `_check_missing`은 UI 스레드에서 `os.path.isdir`를 호출함. 끊긴 네트워크 드라이브가 MD 폴더면 5초마다 잠깐 멈출 수 있음 (미확인)
  - `_error_counts`는 실행 중 계속 유지됨. 같은 오류를 다시 보려면 재시작
  - `config.json`의 잘못된 값은 다음 설정 저장 때 기본값으로 덮어써짐 (경고문에 명시)
  - 미처리로 남긴 것: B4, B8, B9, 포함 관계 MD 폴더의 표시 이름, `.trash` 위치, 실행 중 config 수동 편집, 안 쓰는 코드, CP949→UTF-8 변환(현재 동작 유지)

## 2026-11-10 18:33 Astra 재리뷰 핵심 4건 수정
- 증상:
  1. 4칸 이상 들여쓴 ``` 를 코드블록 시작/끝으로 잘못 인식 → 그 아래 할 일이 안 보이거나, 이월 시 불필요한 닫는 펜스가 붙음
  2. 감시가 끊긴 하위 폴더를 다시 읽다 실패하면 그 폴더의 노트가 달력에서 사라짐
  3. 변경 감시 등록이 재시도까지 실패해도 사용자에게 알림이 없음 (`scan_errors`에만 쌓임)
  4. 테스트의 코드블록 판정 상수 `0x1070`이 틀린 값(실제 `QTextFormat.BlockCodeFence` = 0x1091)이라 검증이 항상 통과
- 원인:
  1. `_FENCE_RE`/`_scan_fences`가 들여쓰기 깊이를 보지 않음
  2. `rescan_dirs`가 `_drop()`으로 먼저 지운 뒤 `_walk()` — 읽기 실패 시 되돌릴 사본이 없음
  3. `_rebuild`가 최종 실패를 시그널로 내보내지 않음
  4. 매직 넘버를 기억으로 적음
- 처리:
  1. `tasks._scan_fences`: 4칸 이상은 목록 항목 안일 때만 펜스로 인정, 닫는 펜스는 여는 펜스보다 3칸 이내
  2. `mdsource._rewalk` 추가: 하위 트리를 `_previous`에 보관 후 재탐색, 실패한 부분은 이전 내용 유지. 읽기 실패 상태(`_unreadable`)인 하위 폴더는 부모 이벤트 때마다 다시 읽기 시도
  3. `_watch_reported` 추가: 최종 실패 경로를 `scanError`로 1회만 알림(트레이)
  4. 테스트를 `QTextFormat.BlockCodeFence` 열거값 + Qt 마크다운 렌더 결과(체크박스 마커) 비교로 교체, 헬퍼 자체 검증 테스트 추가
- 주의:
  - `_rewalk`의 `finally: self._previous = {}` 지우면 다음 스캔 실패 시 엉뚱한 폴더 내용이 되살아남
  - 펜스 규칙을 바꾸면 `test_append_result_renders_as_a_checkbox_not_code`(Qt 렌더러와 일치 검증)로 확인할 것
  - 재리뷰의 나머지(B2 삭제된 루트 복구, B4, B5, B7 편집창 체크박스 펜스 검사, B8, B9)는 요청 범위 밖이라 미처리
  - `test_watcher_signal_updates_index_without_manual_rescan`이 5회 중 1회 `os.replace` PermissionError로 실패(Windows 일시 잠금 추정, 미확인)

## 2026-11-10 18:10 외부 리뷰(GPT-6 Astra) 결함 10건 수정
- 증상 (17:44 작업분 리뷰에서 발견, 9건은 리뷰어가 실행으로 확인):
  1. `-t .` 없이 테스트를 돌리면 `mdcal`이 임시 APPDATA 설정보다 먼저 로드되어 실제 `config.json`을 덮어씀
  2. 칸 입력창에 쓰던 글자가 Enter 없이 다른 칸을 클릭하면 그 날짜로 따라감
  3. 지웠다 다시 만든 폴더는 변경 감시가 끊긴 채로 남음
  4. 폴더 읽기가 일시적으로 실패하면 "삭제됨"으로 처리되어 그 아래 노트와 감시가 사라지고 복구 안 됨
  5. 폴더 링크(junction/symlink) 순환을 따라감
  6. 드라이브 루트(`D:\`)를 MD 폴더로 두면 부분 재스캔이 안 됨
  7. 단축키로 올린 뒤 0.3초 안에 원래 앱으로 돌아가면 달력이 계속 맨 위에 남음
  8. Win+D 상태에서 단축키로 내려도 다음 점검에서 다시 올라옴
  9. 칸에서 추가한 할 일 끝의 `(→ M/D)`가 잘림
  10. 메모가 `~~~`나 4개 이상의 백틱으로 열린 코드 블록으로 끝나면 추가한 할 일이 코드 안에 들어감
- 원인: 17:44 작업의 경계 조건 누락. 9·10은 넘기기용 함수(`append_open_tasks`)를 칸 입력에 그대로 쓴 것
- 처리:
  1. `tests\__init__.py`: `mdcal.config`가 이미 로드돼 있으면 RuntimeError로 중단. 모든 테스트 파일 첫 줄에 `import tests`. 설정을 쓰는 테스트는 `assert_sandboxed()` 호출
  2. `grid.open_quick`: 날짜가 바뀌면 입력창을 비움
  3. `mdsource._rebuild`: 내가 요청한 목록이 아니라 감시기가 실제로 들고 있는 목록(`directories()`/`files()`)과 비교. `rescan_dirs`는 감시가 사라진 기존 하위 폴더를 다시 읽음. 등록 실패는 한 번 더 시도 후 `scan_errors`에 기록
  4. `mdsource`: 읽기 실패(`_scan_failed`)와 없음(FileNotFoundError)을 구분. 읽기 실패 시 마지막 내용과 감시 유지, 폴더당 한 번 `scanError` 신호 → 트레이 알림. 전체 스캔 중 실패해도 이전 내용 유지
  5. `mdsource._scan_dir`: junction과 폴더 symlink는 들어가지 않음(예전 `os.walk`와 같은 동작)
  6. `mdsource._under`: 경로 포함 판정을 한 곳으로 모으고 드라이브 루트 처리
  7. `winpin`: 달력 창의 활성화 이벤트(`grid.changeEvent` → `pin.activated()`)로 미리보기 보호를 해제. 활성화가 안 된 경우에도 1초(`PEEK_GRACE`) 뒤 해제
  8. `winpin`: 단축키로 내리면 `_dismissed`. 바탕화면이 사라질 때까지 자동으로 올리지 않음. 단축키로 다시 올리는 것은 가능
  9. `tasks.append_task`: 입력한 그대로 추가(앞뒤 공백만 제거). 넘기기는 기존 `append_open_tasks` 그대로
  10. `tasks._scan_fences`: 코드 블록을 여는 문자와 길이를 추적. 같은 문자·같은 길이 이상으로만 닫힘. 열린 채 끝나면 같은 구분자로 닫고 추가. `editable_mask`도 같은 판정 사용
- 검증:
  - 테스트 72개 통과(16개 추가). 감시 테스트는 실제 감시기와 실제 파일로 확인(폴더 재생성, tmp→rename 3회, junction 순환)
  - 임시 APPDATA에 가짜 설정을 두고 4가지 방식으로 테스트 실행 → 설정 파일 변동 없음
  - 수정본으로 앱을 다시 시작해 오류 없이 뜨는 것 확인
  - 확인 안 함: 실제 화면에서의 7·8번 동작(테스트로만 확인), 실제 권한 오류 상황
- 주의:
  - 감시기는 삭제된 경로를 자기 이벤트를 처리한 뒤에야 목록에서 뺌. 그 전에는 같은 경로의 제거·재등록이 모두 실패함(실측). 그래서 재등록 실패 시 0.5초 뒤 한 번 더 시도함. 이 재시도를 지우면 다시 만든 폴더의 감시가 끊김
  - `editable_mask`의 코드 블록 판정이 엄격해짐: 예전에는 ``` 와 ~~~ 를 구분 없이 번갈아 열고 닫는 것으로 봤음. 섞어 쓴 메모에서 할 일로 보이는 줄이 달라질 수 있음
  - `mdsource.MdNote.load`의 제목 추출은 예전 방식(단순 토글) 그대로임

## 2026-11-10 17:44 개선 5건: 미리보기 단축키, 칸에서 할 일 추가, 올림 자가 점검, 테스트, 부분 재스캔
- 증상: (신규 기능·개선) 사용자 요청으로 5건 작업
- 원인: -
- 처리:
  1. 미리보기 단축키 (`winpin`, `grid`, `app`, `config`)
     - 설정 `peek_hotkey`(기본 `ctrl+alt+c`, 빈 문자열이면 끔). `RegisterHotKey`로 등록, `WM_HOTKEY`를 `CalendarWindow.nativeEvent`에서 받음
     - 누르면 달력이 모든 창 위로 올라오고 포커스를 받음. 다시 누르거나 다른 앱을 클릭하면 내려감
     - `BottomPin.peek()`: 올린 순간의 활성 창이 그대로인 동안은 내리지 않음(`_peek_fg`). 포커스가 옮겨간 뒤부터 기존 내림 규칙 적용
     - 등록 실패(다른 프로그램이 사용 중, 해석 불가)면 시작 시 경고 한 번. 메뉴에 "맨 위로 잠깐 보기" 추가
  2. 칸에서 할 일 추가 (`grid`, `app`, `detail`)
     - 칸을 클릭하면 칸 아래쪽에 한 줄 입력창. Enter → 그 날짜 메모 끝에 `- [ ] 내용` 추가, 입력창은 열린 채로 비워짐. Esc나 다른 곳 클릭 → 닫힘(안 보낸 글자는 버림)
     - 입력창은 실제 `QLineEdit`(한글 입력기가 첫 글자부터 동작하게). 더블클릭은 그대로 확장 창
     - `app._on_task_added`: 그 날짜 확장 창이 열려 있으면 편집기에 추가(`detail.append_task`, Ctrl+Z 한 번), 아니면 파일에 직접 씀(mtime 충돌 검사). 실패하면 입력한 내용을 경고 창에 그대로 보여줌
  3. 올림 자가 점검 (`winpin.BottomPin`)
     - 올림 상태인데 실제로 맨 위가 아니면 다시 지정. 3회까지, 그래도 안 되면 포기하고(`RAISE FAILED`) 바탕화면이 사라질 때까지 재시도 안 함
     - pin.log는 기본으로 올림/내림/재시도/실패만 기록. 상세 기록은 설정 `pin_debug: true`일 때만(창 목록 수집도 그때만)
  4. 테스트 (`tests\`, 표준 unittest, 56개)
     - `tasks`, `memofile`, `carry`, `mdsource`, `winpin`(단축키 해석, 올림/내림/재시도/미리보기 규칙), 창·설정·확장 창, 칸 입력 처리
     - 실행: `.venv\Scripts\python.exe -m unittest discover -s tests -t .` (임시 APPDATA + offscreen, 실제 설정·메모를 건드리지 않음)
  5. 부분 재스캔 (`mdsource.MdIndex`)
     - 폴더별로 노트를 보관(`_dirs`). 감시 신호가 온 폴더만 다시 읽음(`rescan_dirs`). 새 하위 폴더는 통째로 읽고, 사라진 폴더는 제거
     - 메모를 쓴 뒤에는 메모 폴더만 다시 읽음(`refresh_memos`). 전체 스캔(`rescan`)은 시작·새로고침·폴더 목록 변경 때만
     - 감시 목록은 차이만 반영. 단, 방금 읽은 폴더의 파일은 매번 다시 등록
- 검증:
  - 테스트 56개 통과
  - 실제 앱: 칸 클릭 시 입력창 표시, Ctrl+Alt+C 3회(올림→내림→올림), 다른 앱 클릭 시 내려감, Win+D 시 올라옴 확인
  - 확인 안 함: 실제 한글 입력으로 할 일 추가(실제 메모 파일을 바꾸게 되어 화면에서는 입력하지 않음, 쓰기 경로는 테스트로만 확인), 다른 앱이 활성일 때 단축키, 큰 vault에서의 속도
- 주의:
  - 파일 감시를 폴더 감시만으로 줄이지 말 것: 폴더 감시는 파일 내용 수정 일부를 놓침(실측: 덧붙여 쓰기에서 신호 없음)
  - 파일을 tmp→rename으로 바꿔 쓰면 그 파일의 감시가 풀림. `_rebuild`에서 다시 등록하는 부분을 지우면 두 번째 수정부터 감지 못 함
  - `_peek_fg`를 없애면 단축키로 올리자마자 내려감(올린 시점엔 다른 앱이 활성 상태라서)
  - 칸 클릭이 이제 입력창을 엶. 끄려면 `grid.quick_add = False`(설정 항목은 아직 없음)
  - 올림 상태에서 달력이 포커스를 가진 채로 있으면 다른 앱을 클릭할 때까지 맨 위에 남음(기존 동작과 같음)

## 2026-11-10 17:31 달력이 최소화되면 스스로 복구
- 증상: 달력이 최소화되면(다른 프로그램이 최소화시킨 경우 등) 화면 왼쪽 아래 작은 막대로 남고 되돌릴 방법이 없음. 트레이 더블클릭으로도 안 풀림. 11-10 컴퓨터 제어 테스트 중 실제로 발생
- 원인: 작업표시줄 버튼이 없는 Tool 창이라 복원 수단이 없음. 트레이 더블클릭은 `show()`만 호출(최소화는 안 풀림)
- 처리:
  - `grid.CalendarWindow.changeEvent`: 최소화 상태가 되면 바로 `unminimize()` 호출
  - `unminimize()`: `winpin.restore_no_activate`(SW_SHOWNOACTIVATE, 포커스 안 가져옴)로 복원 후, 올림 상태가 아니면 다시 바닥으로
  - `app._on_tray`: 더블클릭 시 `unminimize()`도 호출
  - `_store_geometry`: 최소화 상태면 저장 안 함(최소화 좌표가 저장되는 것 방지)
- 검증 (실행 중인 달력 창을 밖에서 `ShowWindow(SW_MINIMIZE)`로 최소화, 3회):
  - 최소화 직후 `IsIconic=True` → 약 0.4ms 뒤 복원. 위치·크기 그대로, 포커스 안 가져감, 저장된 위치 변동 없음, 바닥(Progman 바로 위) 유지
- 주의:
  - 달력을 최소화시키는 프로그램이 있으면 달력이 매번 바로 되살아남(의도된 동작). 달력을 치우려면 메뉴의 "숨기기"를 쓸 것
  - Win+M으로 직접 눌러보는 확인은 안 함(같은 최소화 경로일 것으로 추정)
  - 확장 창(`DayDetail`)은 대상이 아님

## 2026-11-10 17:28 코드 리뷰 결함 5건 수정
- 증상 (코드 리뷰로 발견, 실제 발생 보고는 없음):
  1. 창을 옮긴 직후 모니터 신호가 오면 옮긴 위치가 저장 위치로 되돌려짐. 신호 뒤 1.5초 안에 옮긴 위치는 저장 안 됨
  2. 트레이 아이콘의 날짜 숫자가 앱을 시작한 날로 고정됨
  3. 설정 저장 실패 시 메모리의 설정값만 바뀐 채 남음. 위치 저장은 실패할 때마다 오류 창
  4. `reload_notes`에서 메모 파일 stat 오류가 처리되지 않음
  5. 모니터 감시 중복 검사를 `id()`로 해서, id가 재사용되면 새 모니터를 감시하지 않을 수 있음
- 원인: 실패 경로와 타이밍 경합 처리 누락
- 처리:
  1. `grid.CalendarWindow`:
     - `nativeEvent`에서 `WM_EXITSIZEMOVE`(드래그·크기 조절 끝)를 받으면 그 자리에서 위치 저장(`_store_geometry(by_user=True)`, 저장 중지 구간도 무시)
     - 저장 중지 구간(`_hold_save`)에 걸린 타이머 저장은 버리지 않고 다시 예약
     - 위치가 저장값과 같으면 파일을 쓰지 않음
  2. `app._refresh_icon`: `dateChanged`(자정)에 아이콘을 다시 그려 트레이·앱 아이콘에 적용
  3. `config.update_config(cfg, key, value)` 추가: 한 키를 바꿔 저장하고, 실패하면 이전 값으로 되돌린 뒤 OSError를 다시 던짐
     - `app._set_cfg`(주 시작 요일, MD 폴더 추가/제거, 메모 폴더), `grid.set_locked`, `set_opacity`, `_store_geometry`가 사용. 실패하면 경고 후 화면·동작도 바꾸지 않음
     - 위치 저장 실패 경고는 한 번만(`_geo_save_warned`). 저장이 다시 성공하면 초기화
     - `md_folders`는 목록을 제자리에서 고치지 않고 새 목록으로 교체(되돌리기 가능하게)
  4. `detail.reload_notes`: 파일 없음은 None, 그 외 OSError는 경고 한 번(`_stat_warned`) 후 화면 내용 유지
  5. `grid._watch_screen`: `Qt.UniqueConnection`으로 연결. 모니터 신호가 올 때마다 현재 모니터 전부에 다시 호출
- 검증:
  - 임시 APPDATA + offscreen 플랫폼에서 12개 항목 확인(저장 실패 되돌리기, 저장 중지 구간, `WM_EXITSIZEMOVE`, 경고 1회, 신호 중복 없음, stat 오류)
  - 수정본으로 앱을 다시 시작해 오류 없이 뜨는 것 확인
  - 확인 안 함: 실제 마우스 드래그 후 저장, 실제 모니터 뽑기·꽂기, 자정의 아이콘 갱신, `app.py`의 설정 메뉴 실패 경로(컴파일만 확인)
- 주의:
  - 크기 조절 손잡이(QSizeGrip)가 `WM_EXITSIZEMOVE`를 보내는지는 확인하지 않음. 안 보내도 기존 타이머 저장이 그대로 동작함
  - `carry_mode`, `last_carry` 저장은 기존의 자체 되돌리기 코드를 그대로 둠
  - 테스트 스크립트는 저장소에 넣지 않음(임시 폴더에서 실행)

## 2026-11-10 17:17 Win+D 후 달력이 안 올라오는 원인 확정·수정 (SWP_NOOWNERZORDER)
- 증상: Win+D를 눌러도 달력이 가끔 안 올라옴. 실제 Win+D로 재현함(달력을 한 번 클릭한 뒤 Win+D → 매번 실패)
- 원인 (pin.log와 실측으로 확인):
  - 감지와 판정은 정상. `desk_above=True` → `RAISE`까지 감
  - `SetWindowPos(HWND_TOPMOST)`가 성공(ok=True, err=0)을 돌려주지만 실제로는 맨 위가 안 됨(`topmost_after=0`). `HWND_TOP`도 같음
  - 달력이 한 번이라도 포커스를 받으면 달력 창이 숨은 "IME" 창을 소유하게 됨. 이 상태에서 `SWP_NOOWNERZORDER` 없이 호출하면 무시됨
  - 포커스를 받은 적 없는 창은 같은 호출이 성공함 → "가끔"만 실패한 이유
  - 앱은 `raised=True`로 기억하고 다시 시도하지 않아, 올라오지 않은 채로 남음
  - 11:28에 추정한 원인(활성 창 조건)은 이 증상의 원인이 아니었음
- 처리 (`winpin`):
  - `_FLAGS`에 `SWP_NOOWNERZORDER`(0x0200) 추가. `set_topmost`, `send_to_bottom` 모두 적용됨(확장 창 `DayDetail`도 같은 함수 사용)
  - 진단 로그 추가: 줄마다 `topmost=`(실제 맨 위 여부), 올림/내림 때 `SetWindowPos(...) ok= err= topmost_after=` 한 줄
  - `set_topmost`가 성공 여부(bool)를 반환하도록 변경(기존 호출부는 반환값을 안 씀, 영향 없음). `is_topmost()` 추가
- 검증 (실제 Win+D, 17:16~17:17):
  - 칸 클릭 후 Win+D 2회, 헤더 클릭 후 Win+D 1회 → 3회 모두 `topmost_after=1`, 화면에서 달력 보임
  - Win+D로 되돌리면 `LOWER` 후 다시 바닥(Progman 바로 위)으로 내려감
- 주의:
  - `_FLAGS`에서 `SWP_NOOWNERZORDER`를 빼면 이 버그가 되살아남. 제거 금지
  - IME 창 소유가 왜 호출을 무효로 만드는지(Windows 내부 동작)는 확인하지 못함. 플래그 유무에 따른 결과만 실측함
  - 확장 창(`DayDetail`)이 Win+D 상태에서 맨 위로 올라오는지는 따로 확인하지 않음
  - 달력이 최소화되면(Win+M 등) 스스로 복구하지 못함: 창이 화면 왼쪽 아래 작은 막대로 남고, 트레이 더블클릭(`show()`)으로는 안 풀림. 미수정
  - 진단 로그는 원인이 확정됐으므로 줄이거나 꺼도 됨(`diag()`, `_write`의 SetWindowPos 줄)

## 2026-11-10 17:06 pin.log 진단 필드 추가 (Win+D 감지 확인용)
- 증상: 11:28 수정 뒤 pin.log 223줄(11:28~17:02)에 RAISE/LOWER 0회, `desk_above=True` 0회. `fg=Progman`이 2회(11:52:31, 13:04:56) 있었지만 Win+D인지 바탕화면 클릭인지 로그로 구분할 수 없음
- 원인: 로그에 활성 창 클래스와 판정 결과만 있고, 바탕화면 창들의 실제 상태가 없음
- 처리 (`winpin.BottomPin`):
  - `diag()` 추가(기록 전용, 판정에는 쓰지 않음). 로그 줄에 3개 필드 추가
    - `above=`: 달력 바로 위의 보이는 창 클래스
    - `workerw=`: 보이는 WorkerW 개수
    - `defview=`: 아이콘 뷰(SHELLDLL_DefView)가 붙어 있는 창 클래스(Progman/WorkerW)
  - 이 값이 바뀌어도 한 줄 기록(활성 창이 그대로인 Win+D도 잡으려는 것)
  - 평상시 측정값: `above=ApplicationFrameWindow workerw=0 defview=Progman`, 호출 1회 약 0.08ms
- 주의:
  - 올림/내림 판정 로직은 바꾸지 않음. Win+D 미동작이 고쳐진 것은 아님
  - 앱을 다시 시작해야 적용됨
  - Win+D를 눌렀는데 `desk_above=False`로 찍히면 감지 전제(바탕화면 창이 달력 위로 올라옴)가 틀린 것. 그 줄의 `above/workerw/defview` 값으로 새 감지 조건을 정할 것
  - `above=` 값이 자주 바뀌면 로그가 빨리 늘어남(256KB에서 `.1`로 교체). 원인 확정 후 `diag()`와 필드 제거 검토

## 2026-11-10 11:28 Win+D 후 달력이 간헐적으로 안 올라옴 + 전환 로그
- 증상: Win+D를 눌러도 가끔 달력이 안 올라옴. 바탕화면을 클릭해서 한 번 올라오면 그 뒤로는 정상
- 원인 (추정, 실제 재현 안 함):
  - 올리기 조건이 "활성 창 = 바탕화면(Progman/WorkerW) 또는 달력 자신"뿐이었음
  - 그래서 Win+D 뒤 활성 창이 작업표시줄(Shell_TrayWnd 등)로 가거나, 바탕화면에 가려진 직전 앱에 남으면 올리지 않음
  - 바탕화면을 클릭하면 활성 창이 Progman으로 바뀌어 그때 올라옴(증상과 일치)
- 처리 (`winpin.BottomPin`):
  - 올리기: 바탕화면이 달력 위에 있고, 바탕화면 앞에 실제로 보이는 앱이 활성 상태가 아니면 올림
    - 셸 창, 활성 창 없음, 달력 자신, 바탕화면에 가려진 앱이면 올림
  - 내리기: 활성 창이 셸이 아닌 앱이고, 그 앱이 바탕화면 위에 보일 때만(`foreground_hidden()`가 False)
  - `foreground_hidden()`: 활성 창의 최상위 창(GA_ROOT) 위에 보이는 Progman/WorkerW가 있는지 확인
  - 전환 로그: `%APPDATA%\MdCal\pin.log`. 상태가 바뀔 때만 한 줄(시각, 활성 창 클래스, 바탕화면이 위에 있는지, 가려졌는지, 올림/내림). 256KB를 넘으면 `.1`로 교체
  - 로그 기록이 실패하면 기록을 멈추고 경고 창을 한 번 띄움
- 주의:
  - 바탕화면이 달력 위에 있어도 맨 위 고정(TOPMOST) 앱이 활성 상태면 올리지 않음(의도된 동작)
  - 원인이 확정된 건 아님. 증상이 다시 나오면 pin.log에서 그 시각의 `fg=` 값을 확인할 것
  - pin.log는 진단용. 원인이 확정되면 기록을 끄거나 줄이는 것을 검토

## 2026-11-10 11:16 모니터 추가/제거 시 달력 위치 복구
- 증상: 서브 모니터를 뺐다가 다시 꽂거나 단일 모니터로 바뀌어도, 달력이 사라진 모니터 쪽에 남아 보이지 않음
- 원인:
  - 위치 확인을 시작할 때 한 번만 함. 모니터 변경 신호를 처리하지 않음
  - 시작 시 판정이 모든 모니터를 감싼 사각형과 1픽셀만 겹쳐도 통과(모니터 사이 빈 영역도 통과)
  - 모니터가 빠질 때 Windows가 옮긴 좌표가 0.6초 뒤 자동 저장되어 원래 위치를 덮어쓸 수 있음
- 처리 (`grid.CalendarWindow`):
  - `screenAdded`, `screenRemoved`, `primaryScreenChanged`, 각 모니터의 geometry 변경 신호 → 0.5초 뒤 `_ensure_on_screen`
  - `_visible`: 헤더 줄(높이 40)이 실제 어느 모니터에 가로 120px 이상, 세로 20px 이상 보이는지로 판정
  - 저장 위치가 안 보이면 주 모니터 오른쪽 위로 임시 이동(`_fallback`). 이때 위치는 저장하지 않고, 모니터가 돌아오면 원래 위치로 복귀
  - 임시 위치에서 사용자가 직접 옮기면 그 위치를 새로 저장
  - 모니터 변경 직후 1.5초는 위치 자동 저장 중지(`_hold_save`)
- 주의:
  - 실제 모니터를 뽑고 꽂는 확인은 안 함. 가짜 모니터 목록으로 테스트함
  - 모니터별 DPI가 다르면 Windows가 크기를 바꿀 수 있음. 그러면 임시 위치를 사용자 이동으로 오인해 저장할 수 있음(미확인)
  - 확장 창(`DayDetail`)의 위치는 이번 수정 대상이 아님(열 때마다 칸 위치 기준으로 다시 배치됨)
  - 붙여주신 .NET 예외는 MdCal이 아니라 TokenChecker(D:\test)의 것. 이 저장소에서는 처리하지 않음

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
