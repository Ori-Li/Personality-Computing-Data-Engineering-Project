from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from database.dataset_pipeline import analyze_dataset
from database.import_prompt_dataset import DatasetValidationError, validate, validation_issues


def character() -> dict:
    return {
        "id": "realCharacter_usa_1954_001",
        "characterType": 1,
        "realSubType": 1,
        "creativeEntityType": 1,
        "gender": 1,
        "countryCode": "USA",
        "field": 2,
        "dynasty": None,
        "beginCentury": 1954,
        "endCentury": None,
        "names": [{"language": 1, "name": "测试导演"}],
        "introduction": "这位导演长期从事电影创作，通过公开作品呈现稳定的职业活动、媒介选择、合作关系与创作方法。其代表项目重视人物表演、镜头调度和叙事节奏，并在相关类型电影的发展中留下清晰的实践轨迹。",
        "ownWork": [{"workId": "work_usa_1999_001", "relationType": 24}],
    }


def work() -> dict:
    return {
        "workId": "work_usa_1999_001",
        "workName": "测试电影",
        "originalName": "Test Movie",
        "genre": 4,
        "subcategory": 4001,
        "subcategories": [4001, 4005],
        "year": 1999,
        "countryCode": "USA",
        "introduction": "这部电影通过人物关系、镜头调度和叙事节奏展开故事，核心创作者之间的协作共同塑造了作品的整体风格。影片在类型表达与文化主题之间建立联系，并通过具体场景呈现主要冲突。",
        "primaryAuthorId": None,
        "creators": [{
            "characterId": "realCharacter_usa_1954_001",
            "relationType": 24,
            "sortOrder": 0,
        }],
    }


class EntityContractTest(unittest.TestCase):
    def test_actor_relation_is_supported_for_movie(self) -> None:
        characters, works = validate([character()], [work()])
        self.assertEqual(1, len(characters))
        self.assertEqual(1, len(works))

    def test_actor_relation_is_rejected_for_novel(self) -> None:
        item = work()
        item["genre"] = 6
        item["subcategory"] = 6001
        issues = validation_issues([character()], [item])
        self.assertTrue(any("关系 24 与 genre 6 不匹配" in issue for issue in issues))

    def test_bidirectional_relation_must_match(self) -> None:
        item = character()
        item["ownWork"] = []
        issues = validation_issues([item], [work()])
        self.assertTrue(any("ownWork 与作品 creators 不一致" in issue for issue in issues))

    def test_multiple_issues_are_collected(self) -> None:
        item = character()
        item["gender"] = "1"
        item["countryCode"] = "美国"
        with self.assertRaises(DatasetValidationError) as context:
            validate([item], [work()])
        self.assertGreaterEqual(len(context.exception.issues), 2)

    def test_primary_subcategory_must_be_in_tags(self) -> None:
        item = work()
        item["subcategories"] = [4005]
        issues = validation_issues([character()], [item])
        self.assertTrue(any("主分类 subcategory 必须包含" in issue for issue in issues))

    def test_literature_supports_multiple_tags_and_translator(self) -> None:
        person = character()
        person["ownWork"] = [{"workId": "work_usa_1999_001", "relationType": 29}]
        item = work()
        item.update({"genre": 10, "subcategory": 10001, "subcategories": [10001, 10002]})
        item["creators"][0]["relationType"] = 29
        self.assertEqual([], validation_issues([person], [item]))

    def test_music_producer_is_rejected_for_movie(self) -> None:
        person = character()
        person["ownWork"] = [{"workId": "work_usa_1999_001", "relationType": 25}]
        item = work()
        item["creators"][0]["relationType"] = 25
        issues = validation_issues([person], [item])
        self.assertTrue(any("关系 25 与 genre 4 不匹配" in issue for issue in issues))

    def test_analysis_reports_malformed_items_without_crashing(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            characters = root / "characters.json"
            works = root / "works.json"
            characters.write_text('[{"id": ""}, 1]', encoding="utf-8")
            works.write_text('[{"workName": "missing id"}, null]', encoding="utf-8")
            report = analyze_dataset(characters, works)
        self.assertFalse(report["isStructurallyValid"])
        self.assertGreaterEqual(len(report["errors"]), 4)

    def test_v1_logical_id_is_required(self) -> None:
        item = character()
        item["id"] = "realCharacter_eval_001"
        linked = work()
        linked["creators"][0]["characterId"] = item["id"]
        item["ownWork"][0]["workId"] = linked["workId"]
        issues = validation_issues([item], [linked])
        self.assertTrue(any("id 不符合 v1 契约" in issue for issue in issues))

    def test_bce_year_is_supported_in_logical_id(self) -> None:
        item = character()
        item["id"] = "realCharacter_chn_-340_001"
        item["realSubType"] = 2
        item["countryCode"] = "CHN"
        item["field"] = 10
        item["dynasty"] = 5
        item["beginCentury"] = -340
        linked = work()
        linked["creators"][0]["characterId"] = item["id"]
        item["ownWork"][0]["workId"] = linked["workId"]
        issues = validation_issues([item], [linked])
        self.assertFalse(any("id 不符合 v1 契约" in issue for issue in issues))

    def test_unsupported_genre_is_rejected(self) -> None:
        item = work()
        item["genre"] = 13
        item["subcategory"] = None
        issues = validation_issues([character()], [item])
        self.assertTrue(any("genre 非法" in issue for issue in issues))

    def test_sort_order_must_be_contiguous(self) -> None:
        item = work()
        item["creators"][0]["sortOrder"] = 2
        issues = validation_issues([character()], [item])
        self.assertTrue(any("sortOrder 必须从 0 连续递增" in issue for issue in issues))

    def test_begin_year_is_not_a_century_number(self) -> None:
        item = character()
        item["beginCentury"] = 20
        issues = validation_issues([item], [work()])
        self.assertTrue(any("ID 年份必须与 beginCentury=20 一致" in issue for issue in issues))

    def test_end_year_cannot_precede_begin_year(self) -> None:
        item = character()
        item["endCentury"] = 1900
        issues = validation_issues([item], [work()])
        self.assertTrue(any("endCentury 不得早于" in issue for issue in issues))

    def test_chinese_character_requires_period_enum(self) -> None:
        item = character()
        item["realSubType"] = 2
        item["countryCode"] = "CHN"
        item["dynasty"] = None
        issues = validation_issues([item], [work()])
        self.assertTrue(any("dynasty 必须填写" in issue for issue in issues))

    def test_user_facing_introduction_rejects_pipeline_language(self) -> None:
        item = work()
        item["introduction"] = "该实体按照数据库枚举完成建模，用于后续分析和测试，并记录 relationType。"
        issues = validation_issues([character()], [item])
        self.assertTrue(any("数据工程或模板化措辞" in issue for issue in issues))

    def test_user_facing_introduction_rejects_downstream_psychology_framing(self) -> None:
        item = character()
        item["introduction"] = "这些公开作品和职业经历构成了较为完整的创作轨迹，这些事实可用于后续心理分析，但不能替代具体人格判断，也不应脱离人物所处的历史文化环境和实际工作条件。"
        issues = validation_issues([item], [work()])
        self.assertTrue(any("数据工程或模板化措辞" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
