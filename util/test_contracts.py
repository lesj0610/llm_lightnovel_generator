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
    def test_run_ids_unique_within_same_second(self):
        import full_episode_gen as feg
        ids = set()
        for _ in range(5):
            config.current_run_id = ""
            feg._ensure_run_dir()
            ids.add(config.current_run_id)
        self.assertEqual(len(ids), 5)
        # 정리
        import shutil
        for rid in ids:
            shutil.rmtree(os.path.join("result", rid), ignore_errors=True)
        config.current_run_id = ""


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
