from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from database.deepseek_crp_resume import completed_ids, enforce_existing_schema, load_partial_response_array, parse_model_content, update_state


class DeepSeekCrpResumeTest(unittest.TestCase):
    def test_completed_ids_supports_existing_content_id(self) -> None:
        responses = [{"items": [{"basic": {"contentId": "work_001"}}]}]
        self.assertEqual(completed_ids(responses), {"work_001"})

    def test_parse_fenced_response(self) -> None:
        value = parse_model_content('```json\n{"processingState": {}, "items": []}\n```')
        self.assertEqual(value["items"], [])

    def test_parse_repairs_trailing_commas(self) -> None:
        value = parse_model_content('{"processingState": {}, "items": [{"x": 1,}],}')
        self.assertEqual(value["items"], [{"x": 1}])

    def test_parse_repairs_unescaped_quotes_in_string_property(self) -> None:
        content = '''{
  "processingState": {},
  "items": [{
    "explanation": ""不离不弃"等歌词暗示付出。"
  }]
}'''
        value = parse_model_content(content)
        self.assertEqual(value["items"][0]["explanation"], '"不离不弃"等歌词暗示付出。')

    def test_parse_repairs_one_missing_item_closing_brace(self) -> None:
        content = '{\n  "processingState": {},\n  "items": [\n    {"items": []\n  ]\n}'
        value = parse_model_content(content)
        self.assertEqual(value["items"][0]["items"], [])

    def test_existing_schema_lifts_fields_nested_under_semantic(self) -> None:
        affinity = {key: 0.5 for key in (
            "Ni", "Ne", "Ti", "Te", "Fi", "Fe", "Si", "Se", "openness",
            "conscientiousness", "extraversion", "agreeableness", "neuroticism", "Assertive", "Turbulent"
        )}
        item = {
            "basic": {},
            "semantic": {
                "summary": "简介", "themes": [], "keywords": [], "core_experience": "体验",
                "experience_introduction": "体验介绍", "experience_vector": {}, "psychology_vector": {},
                "media_vector": {}, "vector_facts": [],
                "experience_vector_summary": {
                    "experience_description": "体验", "structure_description": "结构",
                    "cognitive_expression": "认知", "behavioral_tendency_description": "行为",
                },
                "personality_affinity": affinity, "supporting_evidence": ["证据"] * 8,
                "quality_control": {},
            },
        }
        enforce_existing_schema(item, {"workId": "work_001"})
        self.assertIn("experience_vector", item)
        self.assertNotIn("experience_vector", item["semantic"])

    def test_update_state_tracks_global_progress(self) -> None:
        value = {"items": []}
        update_state(value, current_id="work_007", next_id="work_008", processed=7, total=114)
        self.assertEqual(value["processingState"]["remaining"], 107)
        self.assertFalse(value["processingState"]["isCompleted"])

    def test_reads_complete_objects_from_array_being_appended(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "progress.json"
            path.write_text(
                '[{"items":[{"basic":{"contentId":"work_001"}}]},'
                '{"items":[{"basic":{"contentId":"work_002"}}]},'
                '{"items":[',
                encoding="utf-8",
            )
            responses = load_partial_response_array(path)
        self.assertEqual(completed_ids(responses), {"work_001", "work_002"})

    def test_existing_schema_removes_new_mapping_and_normalizes_social(self) -> None:
        item = {
            "basic": {}, "semantic": {"summary": "s", "themes": [], "keywords": [], "core_experience": "c", "experience_introduction": "e"}, "experience_vector": {},
            "psychology_vector": {"social": {"love": 0.8}},
            "media_vector": {"music_vector": {"tempo": 0.3}},
            "vector_facts": [
                {"dimensionPath": "psychology_vector.social.love", "score": 0.8, "explanation": "爱情关系突出。"},
                {"imdensionPath": "media_vector.music.tempo", "score": 0.3, "explanation": "速度较慢。"},
                {"dimensionPath": "media_vector.music.genre", "score": 0.5, "explanation": "流行音乐。"},
            ],
            "experience_vector_summary": {
                "experience_description": "体验", "structure_description": "结构",
                "cognitive_expression": "认知", "behavioral_tendency_description": "行为",
            }, "personality_affinity": {key: 0.5 for key in (
                "Ni", "Ne", "Ti", "Te", "Fi", "Fe", "Si", "Se", "openness",
                "conscientiousness", "extraversion", "agreeableness", "neuroticism", "Assertive", "Turbulent"
            )}, "supporting_evidence": [],
            "quality_control": {}, "dimension_evidence": [{"evidenceIndexes": [0]}],
        }
        enforce_existing_schema(item, {"workId": "work_001"})
        self.assertNotIn("dimension_evidence", item)
        self.assertIn("social_relationship", item["psychology_vector"])
        self.assertEqual(
            item["vector_facts"][0]["dimensionPath"],
            "psychology_vector.social_relationship.love",
        )
        self.assertEqual(item["vector_facts"][1]["dimensionPath"], "media_vector.music_vector.tempo")
        self.assertEqual(len(item["vector_facts"]), 2)
        self.assertEqual(item["basic"]["contentId"], "work_001")
        self.assertEqual(completed_ids([{"items": [item]}]), {"work_001"})
        self.assertEqual(item["quality_control"]["trainingStatus"], "REJECTED")
        self.assertTrue(item["quality_control"]["vectorCoveragePassed"])

    def test_existing_schema_rejects_incomplete_summary(self) -> None:
        item = {
            "basic": {}, "semantic": {"summary": "s", "themes": [], "keywords": [], "core_experience": "c", "experience_introduction": "e"}, "experience_vector": {}, "psychology_vector": {},
            "media_vector": {}, "vector_facts": [], "experience_vector_summary": {"summary": "不兼容"},
            "personality_affinity": {key: 0.5 for key in (
                "Ni", "Ne", "Ti", "Te", "Fi", "Fe", "Si", "Se", "openness",
                "conscientiousness", "extraversion", "agreeableness", "neuroticism", "Assertive", "Turbulent"
            )}, "supporting_evidence": [], "quality_control": {},
        }
        with self.assertRaisesRegex(ValueError, "四个非空字段"):
            enforce_existing_schema(item, {"workId": "work_001"})

    def test_existing_schema_derives_missing_experience_introduction(self) -> None:
        item = {
            "basic": {},
            "semantic": {"summary": "事实简介。", "themes": [], "keywords": [], "core_experience": "核心体验。"},
            "experience_vector": {}, "psychology_vector": {}, "media_vector": {}, "vector_facts": [],
            "experience_vector_summary": {
                "experience_description": "体验描述。", "structure_description": "结构描述。",
                "cognitive_expression": "认知描述。", "behavioral_tendency_description": "行为描述。",
            },
            "personality_affinity": {key: 0.5 for key in (
                "Ni", "Ne", "Ti", "Te", "Fi", "Fe", "Si", "Se", "openness",
                "conscientiousness", "extraversion", "agreeableness", "neuroticism", "Assertive", "Turbulent"
            )},
            "supporting_evidence": ["证据"] * 8, "quality_control": {},
        }
        enforce_existing_schema(item, {"workId": "work_001"})
        self.assertEqual(item["semantic"]["experience_introduction"], "事实简介。\n体验描述。")


if __name__ == "__main__":
    unittest.main()
