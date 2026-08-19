"""
계약 단위 테스트 (네트워크 불필요).

실행: python3 -m unittest discover -s util -p "test_*.py"
(저장소 루트에서 실행해야 plot.json 등 상대 경로가 맞습니다)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import quality_gate
import plot_gen
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
        """직렬화 실패 시 기존 복구 파일이 보존돼야 한다 (선삭제 후 쓰기 금지)."""
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
