import unittest
from pathlib import Path

from google.adk.skills import load_skill_from_dir
from google.adk.tools.skill_toolset import SkillToolset

from psyclaw.agent import SKILLS_DIRECTORY, _create_skill_toolset


class SkillsTest(unittest.IsolatedAsyncioTestCase):
    async def test_uses_adk_native_skill_toolset(self) -> None:
        toolset = _create_skill_toolset()

        self.assertIsInstance(toolset, SkillToolset)
        expected_names = {
            "act-therapy",
            "cognitive-behavioral",
            "emotional-support",
            "somatic-grounding",
        }
        self.assertEqual({skill.name for skill in toolset.skills}, expected_names)
        for skill in toolset.skills:
            with self.subTest(skill=skill.name):
                raw_skill = (Path(SKILLS_DIRECTORY) / skill.name / "SKILL.md").read_text(
                    encoding="utf-8"
                )
                self.assertEqual(skill.instructions, raw_skill.split("---", 2)[2].strip())
                self.assertEqual(
                    skill.instructions,
                    load_skill_from_dir(SKILLS_DIRECTORY / skill.name).instructions,
                )
        self.assertEqual(
            {tool.name for tool in await toolset.get_tools()},
            {"list_skills", "load_skill", "load_skill_resource", "run_skill_script"},
        )


if __name__ == "__main__":
    unittest.main()
