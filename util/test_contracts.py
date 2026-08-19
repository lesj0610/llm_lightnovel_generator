"""
계약 단위 테스트 (네트워크 불필요).

실행: python3 -m unittest discover -s util -p "test_*.py"
(저장소 루트에서 실행해야 plot.json 등 상대 경로가 맞습니다)
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import quality_gate
import plot_gen
import character_setup
import llm_novel_gui_func as gf
import openAPI_control as oc


class TestSplitEpisodes(unittest.TestCase):
    def test_plot_gen_format(self):
        """plot_gen 출력 형식 '##EPISODE N:'을 인식해야 한다 (기존 오파싱 버그)."""
        text = "##EPISODE 1: 첫 만남\n내용1\n##EPISODE 2: 재회\n내용2"
        eps = gf.split_episodes(text)
        self.assertEqual(len(eps), 2)
        self.assertIn("첫 만남", eps[0])
        self.assertIn("재회", eps[1])

    def test_story_gen_format(self):
        """기존 '## EPISODE N ##' 형식도 계속 인식해야 한다."""
        text = "## EPISODE 1 ##\n내용1\n## EPISODE 2 ##\n내용2"
        eps = gf.split_episodes(text)
        self.assertEqual(len(eps), 2)

    def test_missing_number_gap(self):
        """누락 번호는 빈 문자열 슬롯으로 채워 인덱스=번호 대응을 유지한다."""
        text = "##EPISODE 1: a\n본문a\n##EPISODE 3: c\n본문c"
        eps = gf.split_episodes(text)
        self.assertEqual(len(eps), 3)
        self.assertEqual(eps[1], "")

    def test_no_header(self):
        self.assertEqual(gf.split_episodes("그냥 텍스트"), ["그냥 텍스트"])
        self.assertEqual(gf.split_episodes(""), [])


class TestParseEpisodes(unittest.TestCase):
    def test_padding_and_missing(self):
        eps = plot_gen.parse_episodes("EPISODE 1: aaa\nEPISODE 3: ccc", 3)
        self.assertEqual(len(eps), 3)
        self.assertEqual(eps[1], "")

    def test_refine_marks_missing(self):
        """누락 에피소드는 범용 문장으로 은폐하지 않고 명시 표시."""
        tpl = {"flow": "a -> b", "ending": "end"}
        refined = plot_gen.review_and_refine(["내용1", "", "내용3"], tpl, [], 3)
        self.assertIn("생성 누락", refined[1])


class TestQualityGate(unittest.TestCase):
    def test_detects_leaks_and_repetition(self):
        bad = "나는 그녀의 허리를 감쌌다. " * 6 + "[기] 파트에서의 접촉. 방어기제가 무너졌다."
        issues = quality_gate.check_section(bad, "결")
        joined = " ".join(issues)
        self.assertIn("동어반복", joined)
        self.assertIn("라벨", joined)
        self.assertIn("지시 용어", joined)

    def test_final_check_blocks_missing_section(self):
        ok, hard, soft = quality_gate.final_check(
            {"기": "본문" * 500, "승": "본문" * 900, "전": "본문" * 900, "결": ""})
        self.assertFalse(ok)
        self.assertTrue(any("결" in h for h in hard))


class TestConfigContracts(unittest.TestCase):
    def test_json_value_is_live(self):
        v1 = config.json_value
        v2 = config.json_value
        self.assertIsNot(v1, v2)

    def test_restore_skips_json_value_shadow(self):
        import tempfile, yaml
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False,
                                         encoding="utf-8") as f:
            yaml.dump({"json_value": {"ip_main": "stale-host"}, "love_value": 3}, f,
                      allow_unicode=True)
            path = f.name
        try:
            ok, _ = gf.restore_config_from_file(path)
            self.assertTrue(ok)
            self.assertNotIn("json_value", vars(config))
            self.assertNotEqual(config.json_value.get("ip_main"), "stale-host")
            self.assertEqual(config.love_value, 3)
        finally:
            os.unlink(path)

    def test_history_reset_and_trim(self):
        config.reset_messages_history()
        self.assertEqual(len(config.messages_history), 1)
        config.messages_history += [
            {"role": "user", "content": str(i)} for i in range(300)]
        config.trim_messages_history()
        self.assertEqual(len(config.messages_history), 1 + config.MAX_HISTORY_MESSAGES)
        self.assertEqual(config.messages_history[0]["role"], "system")


class TestConfigLoaders(unittest.TestCase):
    """모든 복구 경로가 json_value shadow를 차단해야 한다."""

    def test_load_config_export_filters_json_value(self):
        applied = gf._apply_saved_config_vars(
            {"json_value": {"ip_main": "stale-host"}, "love_value": 7})
        self.assertEqual(applied, 1)
        self.assertNotIn("json_value", vars(config))
        self.assertNotEqual(config.json_value.get("ip_main"), "stale-host")
        self.assertEqual(config.love_value, 7)

    def test_filter_removes_existing_shadow(self):
        config.__dict__["json_value"] = {"ip_main": "old-shadow"}
        gf._apply_saved_config_vars({"love_value": 1})
        self.assertNotIn("json_value", vars(config))


class TestRunId(unittest.TestCase):
    """임시 디렉토리에서 실행 — 실제 result/latest를 절대 건드리지 않는다."""

    def setUp(self):
        import tempfile
        self._old_cwd = os.getcwd()
        self._old_run_id = config.current_run_id
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        self.addCleanup(self._restore)

    def _restore(self):
        os.chdir(self._old_cwd)
        config.current_run_id = self._old_run_id
        self._tmp.cleanup()

    def test_run_ids_unique_within_same_second(self):
        import full_episode_gen as feg
        ids = set()
        for _ in range(5):
            config.current_run_id = ""
            feg._ensure_run_dir()
            ids.add(config.current_run_id)
        self.assertEqual(len(ids), 5)
        # latest 포인터는 임시 디렉토리 안에만 생성됐는지 확인
        self.assertTrue(os.path.isfile(os.path.join("result", "latest")))


class TestArchiveAndExportContracts(unittest.TestCase):
    def test_archive_copies_md_without_network(self):
        """(ok, msg) 계약 + md 실복사 검증. ARCHIVE_PNG=False에서는 ComfyUI
        큐 폴링(urlopen)도 실행되면 안 된다 — urlopen 호출 시 즉시 실패."""
        import tempfile
        import urllib.request
        from unittest import mock
        old_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                os.makedirs("result", exist_ok=True)
                with open(os.path.join("result", "episode_01.md"), "w", encoding="utf-8") as f:
                    f.write("# Episode 1\n\n내용")
                with mock.patch.object(urllib.request, "urlopen",
                                       side_effect=AssertionError("네트워크 접근 금지")), \
                     mock.patch.object(gf.time, "sleep",
                                       side_effect=AssertionError("대기 금지")):
                    ok, msg = gf.archive_to_done(comfyui_dir=os.path.join(tmp, "없는경로"))
                self.assertTrue(ok, msg)
                self.assertIn("아카이브 완료", msg)
                self.assertTrue(os.path.isfile(os.path.join("done", "book1", "episode_01.md")))
            finally:
                os.chdir(old_cwd)

    def test_export_returns_tuple_failure(self):
        ok, msg = gf.export_config_to_file("/없는디렉토리/x/y.yaml")
        self.assertFalse(ok)
        self.assertIn("실패", msg)

    def test_export_atomic_keeps_previous_on_failure(self):
        """직렬화 실패 시 기존 복구 파일 보존 + 부분 .tmp 잔존물 없음."""
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config_export.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write("love_value: 1\n")
            with mock.patch.object(gf.yaml, "dump", side_effect=RuntimeError("직렬화 실패")):
                ok, msg = gf.export_config_to_file(path)
            self.assertFalse(ok)
            with open(path, encoding="utf-8") as f:
                self.assertIn("love_value: 1", f.read())
            leftovers = [f for f in os.listdir(tmp) if f.endswith(".tmp")]
            self.assertEqual(leftovers, [], "실패 시 부분 .tmp가 남으면 안 됨")

    def test_archive_png_preserves_subdir_duplicates(self):
        """ARCHIVE_PNG 활성 경로: 하위 디렉토리별 동명 PNG가 덮어써지지 않고
        상대 경로로 보존돼야 하며 보고 개수와 실제 파일 수가 일치해야 한다."""
        import tempfile
        import urllib.request
        from unittest import mock
        old_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                os.makedirs("result", exist_ok=True)
                with open(os.path.join("result", "episode_01.md"), "w", encoding="utf-8") as f:
                    f.write("# Episode 1\n\n내용")
                comfy = os.path.join(tmp, "comfy")
                for sub in ("a", "b"):
                    os.makedirs(os.path.join(comfy, sub))
                    with open(os.path.join(comfy, sub, "same.png"), "wb") as f:
                        f.write(sub.encode())
                from urllib.error import URLError
                with mock.patch.object(gf, "ARCHIVE_PNG", True), \
                     mock.patch.object(urllib.request, "urlopen",
                                       side_effect=URLError("연결 없음")), \
                     mock.patch.object(gf.time, "sleep"):
                    ok, msg = gf.archive_to_done(comfyui_dir=comfy)
                self.assertTrue(ok, msg)
                self.assertIn("png=2개", msg)
                a = os.path.join("done", "book1", "a", "same.png")
                b = os.path.join("done", "book1", "b", "same.png")
                self.assertTrue(os.path.isfile(a) and os.path.isfile(b))
                with open(a, "rb") as f:
                    self.assertEqual(f.read(), b"a")
                with open(b, "rb") as f:
                    self.assertEqual(f.read(), b"b")
            finally:
                os.chdir(old_cwd)


class TestFinalizeAutoRun(unittest.TestCase):
    """자동 실행 마무리 계약: 완료 선언은 아카이브 성공 시에만."""

    def _run(self, archive_result):
        from unittest import mock
        cb_calls = []
        with mock.patch.object(gf, "archive_to_done", return_value=archive_result):
            result = gf._finalize_auto_run(
                cb=lambda step, status, text: cb_calls.append((step, status, text)),
                anima_enb=False, current_file_index=0)
        return result, cb_calls

    def test_archive_failure_is_partial_without_complete_claim(self):
        result, cb_calls = self._run((False, "아카이브 실패: 디스크 오류"))
        self.assertFalse(result["success"])
        self.assertTrue(result["content_text"].startswith("[전체 자동 실행 부분 완료"))
        self.assertNotIn("[전체 자동 실행 완료]", result["content_text"])
        statuses = [s for _, s, _ in cb_calls]
        self.assertIn("부분 완료 (아카이브 실패)", statuses)
        self.assertNotIn("전체 자동 실행 완료", statuses)

    def test_archive_success_declares_complete(self):
        result, cb_calls = self._run((True, "아카이브 완료: done/book1"))
        self.assertTrue(result["success"])
        self.assertIn("[전체 자동 실행 완료]", result["content_text"])
        self.assertIn("전체 자동 실행 완료", [s for _, s, _ in cb_calls])


class TestGenerateRefineContract(unittest.TestCase):
    def test_refine_failure_not_success(self):
        """episode_summary_gen 실패가 성공(이전 내용 표시)으로 둔갑하면 안 된다."""
        from unittest import mock
        import story_gen
        with mock.patch.object(story_gen, "episode_summary_gen",
                               side_effect=RuntimeError("API 실패")):
            result = gf.generate_refine()
        self.assertFalse(result["success"])
        self.assertIn("API 실패", result["out_txt"])
        self.assertEqual(result["episode_count"], 0)


class TestCharacterGen(unittest.TestCase):
    """캐릭터 설정: 사용자 지정 > LLM 추론 > 랜덤 우선순위와 검증 계약."""

    def setUp(self):
        import character_gen
        self.cg = character_gen
        self.candidates = character_gen.load_candidates()
        config.locked_fields.clear()
        self.addCleanup(config.locked_fields.clear)

    def test_personality_names_have_no_hash(self):
        """'###순수/평범'처럼 샵이 3개인 항목도 이름만 추출돼야 personality.txt 조회가 된다."""
        names = self.candidates["personality_real"]
        self.assertTrue(names)
        self.assertFalse([n for n in names if n.startswith("#")])
        self.assertIn("야마토 나데시코", names)

    def test_index_field_returns_int_tag_field_returns_str(self):
        # body_dic 인덱스로 소비되는 필드는 정수
        ok, v, _ = self.cg.validate_choice("body_size", 3, self.candidates)
        self.assertTrue(ok)
        self.assertIsInstance(v, int)
        # 태그 문자열로 소비되는 필드는 문자열
        ok, v, _ = self.cg.validate_choice("eye_color", 0, self.candidates)
        self.assertTrue(ok)
        self.assertIsInstance(v, str)
        self.assertIn(v, self.candidates["eye_color"])

    def test_face_style_is_string_not_index(self):
        """face_style은 character_init이 눈썹 태그를 이어붙이므로 문자열이어야 한다
        (정수로 저장하면 'int += str' TypeError)."""
        ok, v, _ = self.cg.validate_choice("face_style", 3, self.candidates)
        self.assertTrue(ok)
        self.assertIsInstance(v, str)
        self.assertIn(v, self.candidates["face_style"])

    def test_out_of_range_and_unknown_value_rejected(self):
        ok, _, reason = self.cg.validate_choice("eye_color", 9999, self.candidates)
        self.assertFalse(ok)
        self.assertIn("범위", reason)
        ok, _, reason = self.cg.validate_choice("eye_color", "무지개색 눈", self.candidates)
        self.assertFalse(ok)
        self.assertIn("후보에 없는", reason)

    def test_empty_free_text_rejected_except_note(self):
        ok, _, reason = self.cg.validate_choice("job2", "", self.candidates)
        self.assertFalse(ok, "빈 자유 서술은 성공으로 치면 안 됨")
        self.assertIn("빈 값", reason)
        ok, v, _ = self.cg.validate_choice("appearance_note", "", self.candidates)
        self.assertTrue(ok, "특이사항은 비어 있을 수 있음")

    def test_user_spec_wins_and_locks(self):
        """사용자 지정값은 LLM 호출 없이 반영되고 이후 랜덤에 덮이지 않는다."""
        import tempfile
        from unittest import mock
        spec = {"protagonist": {"sex": "남자", "age": 31, "job": "형사",
                                "appearance": "", "personality": ""},
                "partner": {"sex": "여자", "age": 28, "job": "검사",
                            "appearance": "단정한 정장", "personality": "냉철함",
                            "talking_style": "건조한 말투"},
                "on_mapping_failure": "random"}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False)
            path = f.name
        try:
            with mock.patch.object(self.cg, "_request_mapping",
                                   return_value=(None, "테스트: LLM 미사용")):
                result = self.cg.apply_character_spec(path=path)
            self.assertEqual(config.sex, "남자")
            self.assertEqual(config.age, 31)
            self.assertEqual(config.job, "형사")
            self.assertEqual(config.sex2, "여자")
            self.assertTrue(config.is_locked("sex"))
            # 성별 하드코딩 경로가 잠금을 존중하는지
            character_setup.random_setup_all()
            self.assertEqual(config.sex, "남자", "random_setup_all이 사용자 성별을 덮어씀")
            self.assertEqual(config.job, "형사")
        finally:
            os.unlink(path)

    def test_llm_failure_falls_back_to_random_not_silent(self):
        """LLM 전면 실패 시 랜덤으로 채우되, 실패 내역을 보고해야 한다."""
        import tempfile
        from unittest import mock
        spec = {"protagonist": {"sex": "여자", "age": 20, "job": "학생"},
                "partner": {}, "on_mapping_failure": "random"}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False)
            path = f.name
        try:
            with mock.patch.object(self.cg, "_request_mapping",
                                   return_value=(None, "서버 연결 실패")):
                result = self.cg.apply_character_spec(path=path)
            self.assertTrue(result["failed"], "실패를 조용히 숨기면 안 됨")
            self.assertTrue(all(f.get("resolved_by") == "random" for f in result["failed"]))
            self.assertIn(config.eye_color, self.candidates["eye_color"])
            report = self.cg.format_failure_report(result["failed"])
            self.assertIn("서버 연결 실패", report)
        finally:
            os.unlink(path)

    def test_ask_policy_defers_to_caller(self):
        import tempfile
        from unittest import mock
        spec = {"protagonist": {"sex": "여자"}, "partner": {},
                "on_mapping_failure": "ask"}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False)
            path = f.name
        try:
            with mock.patch.object(self.cg, "_request_mapping",
                                   return_value=(None, "실패")):
                result = self.cg.apply_character_spec(path=path)
            self.assertTrue(result["needs_user_choice"])
            self.assertTrue(result["failed"])
            # 수동 선택 반영
            applied, rejected = self.cg.apply_manual_choices({"eye_color": 0})
            self.assertEqual(rejected, [])
            self.assertEqual(config.eye_color, self.candidates["eye_color"][0])
        finally:
            os.unlink(path)

    def test_free_personality_does_not_pollute_classifier(self):
        """자유 성격 서술이 personality_real(20종 분류 키)로 직행하면
        personality.txt 상세 조회가 실패한다 — 반드시 분리돼야 한다."""
        import tempfile
        from unittest import mock
        spec = {"protagonist": {"sex": "여자", "age": 22, "job": "아이돌",
                                "personality": "겉으로는 침착하지만 속으로는 외로움을 많이 탄다"},
                "partner": {}, "on_mapping_failure": "random"}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False)
            path = f.name
        try:
            with mock.patch.object(self.cg, "_request_mapping",
                                   return_value=(None, "LLM 미사용")):
                self.cg.apply_character_spec(path=path)
            # 서술이 분류 키를 오염시키지 않아야 한다
            self.assertNotIn("침착", config.personality_real or "")
            # 랜덤 폴백이라도 분류는 20종 중 하나여야 상세 조회가 된다
            self.assertIn(config.personality_real, self.candidates["personality_real"])
            character_setup.personality_init(config.json_value)
            self.assertTrue(config.personality_text,
                            "personality.txt 상세 조회가 비었음")
        finally:
            os.unlink(path)

    def test_free_text_fields_are_not_in_direct_mapping(self):
        """appearance/personality는 config로 직결되지 않는다 (LLM 정규화 경유)."""
        proto_map = self.cg._SPEC_TO_CONFIG["protagonist"]
        self.assertNotIn("personality", proto_map)
        self.assertNotIn("appearance", proto_map)
        partner_map = self.cg._SPEC_TO_CONFIG["partner"]
        self.assertNotIn("personality", partner_map)
        self.assertNotIn("appearance", partner_map)

    def test_note_fields_requested_only_when_described(self):
        needed = self.cg._needed_fields(
            {"appearance": "긴 머리", "personality": "차분함"}, {})
        self.assertIn("appearance_note", needed)
        self.assertIn("personality_note", needed)
        needed = self.cg._needed_fields({}, {})
        self.assertNotIn("appearance_note", needed)
        self.assertNotIn("personality_note", needed)

    def test_missing_file_is_not_an_error(self):
        result = self.cg.apply_character_spec(path="/없는경로/character.json")
        self.assertEqual(result["applied"], {})
        self.assertEqual(result["failed"], [])


class TestApiKey(unittest.TestCase):
    def test_env_priority_and_env_file(self):
        old = os.environ.pop("OPENAI_API_KEY", None)
        try:
            env_path = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), ".env")
            had_env_file = os.path.exists(env_path)
            if not had_env_file:
                with self.assertRaises(oc.LLMRequestError):
                    oc.get_api_key()
            os.environ["OPENAI_API_KEY"] = "env-wins"
            self.assertEqual(oc.get_api_key(), "env-wins")
        finally:
            if old is not None:
                os.environ["OPENAI_API_KEY"] = old
            else:
                os.environ.pop("OPENAI_API_KEY", None)


if __name__ == "__main__":
    unittest.main()
