import unittest

from astrbot.core.pipeline.result_decorate.stage import ResultDecorateStage


class SegmentedReplyTests(unittest.TestCase):
    def stage(self) -> ResultDecorateStage:
        stage = object.__new__(ResultDecorateStage)
        stage.split_mode = "regex"
        stage.regex = r".*?[。？！~…]+|.+$"
        stage.split_words_pattern = None
        stage.split_words = ["。", "？", "！", "~", "…"]
        return stage

    def test_explicit_newlines_become_separate_messages(self) -> None:
        self.assertEqual(
            self.stage()._split_plain_text("第一段没有句号\n第二段也没有句号"),
            ["第一段没有句号", "第二段也没有句号"],
        )

    def test_long_text_is_still_segmented_by_punctuation(self) -> None:
        text = "第一句。" + ("很长" * 100) + "第二句！"
        result = self.stage()._split_plain_text(text)
        self.assertEqual(len(result), 2)
        self.assertTrue(result[0].endswith("。"))
        self.assertTrue(result[1].endswith("！"))

    def test_ecobot_segment_limit_preserves_all_text(self) -> None:
        segments = [f"第{index}段。" for index in range(1, 8)]
        limited = self.stage()._limit_segments(segments, 5)

        self.assertEqual(len(limited), 5)
        self.assertEqual("".join(limited), "".join(segments))
