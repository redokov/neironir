## Execution Log

| Task | Status | Evidence |
|------|--------|----------|
| T1 | Done | ValueError вместо обрезки; test_docx.py: 6 passed (test_build_rejects_replacement_crossing_paragraph_boundary, test_build_still_clips_at_element_bounds, test_reject_count_is_recorded_in_stats) |
| T2 | Done | hyperlink fallback: runs[0] text + else: element.text; test_build_preserves_text_of_hyperlink_paragraph (1 passed) |
| T3 | Done | Recursive element map (w:p через вложенные w:tbl); test_nested_table_pii_is_extracted_and_replaced (12 passed edge_cases); VERIFY_OK |
| T4 | Done | submit_feedback: load_job+COMPLETED check; 404 unknown / 409 not-ready / 409 corrupted; 3 tests pass; VERIFY_OK |
| T5 | Done | applied_actions.json history + result.orig snapshot; repeat apply = no-op (early return, no dataset dup); test_apply_feedback_is_idempotent_on_repeat + 15 unit tests pass; VERIFY_OK |
| T6 | Done | OverlappingActionsError + 400 before write; feedback.json removed on refusal; 2 overlap tests pass (400+nothing written, non-overlap accepted); VERIFY_OK |
| T7 | Done | strict positional alignment dict + AlignmentMismatchError (counts/types/offsets verified vs source); reject by annotation index; 409 endpoint mapping; 2 unit + 1 integration test pass; VERIFY_OK |
| T8 | Done | FeedbackItemIn: Literal action, EntityType enum, ge=0 + start<=end validator; 422 on malformed (9 unit + 3 integration tests); canonical _action_key fixes dedup; VERIFY_OK |
| T9 | Done | login.js safeNext allowlist (scheme/protocol-relative/backslash/control-char rejected, fallback /admin) + server-side 4 tests; node eval verified; VERIFY_OK |
| T10 | Done | uploadSeq token in app.js upload(); guards after every await (catch/res.ok/currentJobId); node --check OK; static regression test; VERIFY_OK |
| T11 | Done | reviewSeq token + myJobId snapshot in openReview; guards after each await; closeReview bumps token; static regression test; VERIFY_OK |
| T12 | Done | #24 reviewBtn only md; #25 PRIVATE_ORGANIZATION prefix; #26 private_url toolbar; #27 stopTrainingPolling on final+beforeunload; 5 static tests pass; VERIFY_OK |
| T13 | Done | _origin_matches strict: scheme+netloc required, Origin:null/referer:null -> 403, no headers -> 403; fixtures/e2e send same-origin Origin; 5 new tests + full suite green; VERIFY_OK |
| T14 | Done | save_result: _ALLOWED_EXTS allowlist + resolved-path parent check; 3 storage unit tests (traversal, dotdot-encoded, happy path); VERIFY_OK |
| T15 | Done | is_valid_api_key OR-accumulator over all keys (no any() short-circuit); 3 new tests (compare count first-match/no-match + behaviour); VERIFY_OK |
| T16 | Done | _terminate_then_kill (SIGTERM->grace wait->SIGKILL->reap) + finally cancels/drains readers, feed_eof + transport close; 3 tests incl real SIGTERM-ignoring child; VERIFY_OK |
| T17 | Done | download: result_path.is_file guard -> 404 result_missing (both binary+JSON branches); 2 integration tests; VERIFY_OK |

Формат Status: `Done` / `blocked (причина)`. Evidence — вывод финальной
верификации задачи (ruff/mypy/pytest).

## Прогон 2026-11-06 (оркестратор scripts/night_orchestrator.py)

Все T1–T17 выполнены по паттерну оркестратора (линейный пайплайн задач,
верификация ruff+mypy+pytest после каждой, прогресс — в таблицу выше и
heartbeat `docs/night-006-heartbeat.log`).

Дополнительно исправлены два предсуществующих падения, блокировавших
зелёную верификацию:

- deadlock `_probe_subprocess()` при вызове изнутри event loop — combined
  режим молча деградировал в mock на Windows (test_subprocess_fallback);
- константа `starlette.status.HTTP_413_CONTENT_TOO_LARGE` отсутствует в
  установленном starlette 0.38 — заменена литералом 413 (тесты 413).

Финальная верификация: ruff OK, mypy OK, pytest **504 passed / 21 skipped /
0 failed** (маркер real_model исключён, как предписано планом).

