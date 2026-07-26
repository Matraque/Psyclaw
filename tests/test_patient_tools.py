import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from psyclaw import patient_tools


class PatientToolsTest(unittest.TestCase):
    def test_references_are_loaded_first(self) -> None:
        self.assertEqual(patient_tools.CORE_RECORD_PATHS[0], "references.md")

    def test_public_defaults_include_every_core_record(self) -> None:
        for record_path in patient_tools.CORE_RECORD_PATHS:
            with self.subTest(record_path=record_path):
                self.assertTrue(
                    (patient_tools.DEFAULT_PATIENT_FILES_DIRECTORY / record_path).is_file()
                )

    def test_patient_root_is_shared_across_import_and_environment_order(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            for order in ("before", "after"):
                target = Path(temporary_directory) / order / "patient"
                script = """
import json
import os
import sys

target, order = sys.argv[1], sys.argv[2]
if order == "before":
    os.environ["PSYCLAW_PATIENT_DIR"] = target
from psyclaw import patient_tools
from psyclaw import transcript
if order == "after":
    os.environ["PSYCLAW_PATIENT_DIR"] = target
print(json.dumps({
    "tools": str(patient_tools._patient_root()),
    "transcript": str(transcript.configured_patient_root()),
}))
"""
                environment = os.environ.copy()
                environment.pop("PSYCLAW_PATIENT_DIR", None)
                completed = subprocess.run(
                    [sys.executable, "-c", script, str(target), order],
                    cwd=project_root,
                    env=environment,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                result = json.loads(completed.stdout.splitlines()[-1])
                with self.subTest(order=order):
                    expected = str(target.resolve())
                    self.assertEqual(result, {"tools": expected, "transcript": expected})


if __name__ == "__main__":
    unittest.main()
