"""Offline contracts; known bugs are explicit expected failures until fixed.

Set NIW_STRICT_REGRESSIONS=1 to make the known bugs fail the command.
All executions use temporary synthetic evidence and an isolated tool PATH.
"""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
known_defect = (
    (lambda test: test)
    if os.environ.get("NIW_STRICT_REGRESSIONS") == "1"
    else unittest.expectedFailure
)


class PipelineContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="niw-regression-")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.bin = self.work / "bin"
        self.bin.mkdir()
        # Do not inherit a real Claude CLI, PDF extractor, or user configuration.
        for tool in ("ls", "basename", "date", "mkdir", "find", "wc", "tr",
                     "cat", "head", "grep", "sed", "cut", "awk", "python3"):
            resolved = shutil.which(tool)
            if resolved is None:
                self.fail(f"Required test utility missing: {tool}")
            (self.bin / tool).symlink_to(resolved)
        self.project = self.work / "project"
        (self.project / "src").mkdir(parents=True)
        shutil.copyfile(ROOT / "src/prepare-run.py", self.project / "src/prepare-run.py")
        for relative in (
            ".claude/commands/niw-run.md",
            ".claude/skills/document-summary-arrangement/SKILL.md",
            ".claude/skills/document-summary-arrangement/scripts/scan-collection.sh",
            ".claude/skills/niw-national-importance-research/SKILL.md",
            ".claude/skills/niw-federal-evidence-harvester/SKILL.md",
            ".claude/skills/niw-future-endeavors/SKILL.md",
        ):
            fixture = self.project / relative
            fixture.parent.mkdir(parents=True, exist_ok=True)
            fixture.write_text("Synthetic test dependency\n")
            if fixture.suffix == ".sh":
                fixture.chmod(0o755)
        self.env = {
            "PATH": str(self.bin),
            "HOME": str(self.work),
            "NIW_GURU_HOME": str(self.project),
            "LC_ALL": "C",
        }
        self.source = self.work / "synthetic source"
        self.source.mkdir()
        (self.source / "cv.txt").write_text("Fictional researcher.\n")
        self.output = self.work / "output"
        self.marker = self.work / "claude-called"
        self.env["NIW_TEST_MARKER"] = str(self.marker)
        self.launcher = self.work / "niw-guru"
        self.launcher.write_text(
            (ROOT / "src/niw-guru.in").read_text().replace("@NIW_GURU_HOME@", str(ROOT))
        )

    def tool(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)

    def fake_claude(self, status=0):
        self.tool("claude", f': > "$NIW_TEST_MARKER"\nexit {status}\n')

    def launch(self, *args, output=None, name="run"):
        return subprocess.run(
            [BASH, str(self.launcher), "-s", str(self.source),
             "-o", str(output or self.output), "-n", name, *args],
            env=self.env, cwd=self.work, capture_output=True, text=True, timeout=10,
        )

    def test_dry_run_without_claude_creates_no_output(self):
        result = self.launch("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[dry-run]", result.stdout)
        self.assertFalse(self.output.exists())

    def test_built_launcher_keeps_baked_root_without_override(self):
        del self.env["NIW_GURU_HOME"]
        result = self.launch("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("project : " + str(ROOT.resolve()), result.stdout)
        self.assertFalse(self.output.exists())

    def test_dry_run_never_invokes_available_claude(self):
        self.fake_claude(99)
        result = self.launch("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.output.exists())

    def test_real_run_without_claude_fails_before_writing(self):
        result = self.launch()
        self.assertEqual(result.returncode, 127, result.stderr)
        self.assertIn("CLI was not found", result.stderr)
        self.assertFalse(self.output.exists())

    def test_dry_run_still_rejects_missing_source(self):
        (self.source / "cv.txt").unlink()
        self.source.rmdir()
        result = self.launch("--dry-run")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source is not a directory", result.stderr)
        self.assertFalse(self.output.exists())

    def test_dry_run_still_rejects_empty_source(self):
        (self.source / "cv.txt").unlink()
        result = self.launch("--dry-run")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source directory is empty", result.stderr)

    def test_claude_failure_is_propagated(self):
        self.fake_claude(7)
        result = self.launch()
        self.assertTrue(self.marker.exists(), result.stderr)
        self.assertEqual(result.returncode, 7, result.stderr)

    def test_source_overlap_is_rejected_before_any_write(self):
        self.fake_claude()
        before = sorted(p.relative_to(self.source) for p in self.source.rglob("*"))
        result = self.launch(output=self.source)
        after = sorted(p.relative_to(self.source) for p in self.source.rglob("*"))
        self.assertEqual(after, before, "Launcher wrote into its read-only source")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())

    def test_run_name_cannot_escape_output_root(self):
        self.fake_claude()
        result = self.launch(name="../escaped")
        self.assertFalse((self.work / "escaped").exists(), "Run escaped output root")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())

    def assert_rejected(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.marker.exists(), "Claude started for invalid configuration")

    def test_symlink_output_into_source_is_rejected(self):
        self.fake_claude()
        alias = self.work / "alias"
        alias.symlink_to(self.source, target_is_directory=True)
        self.assert_rejected(self.launch(output=alias / "new"))
        self.assertFalse((self.source / "new").exists())

    def test_source_symlink_cannot_hide_overlap(self):
        self.fake_claude()
        original = self.source
        self.source = self.work / "source-alias"
        self.source.symlink_to(original, target_is_directory=True)
        self.assert_rejected(self.launch(output=original / "nested"))
        self.assertFalse((original / "nested").exists())

    def test_existing_run_and_symlinks_are_never_reused(self):
        self.fake_claude()
        self.output.mkdir()
        run = self.output / "run"
        run.mkdir()
        sentinel = run / ".niw-run.env"
        sentinel.write_text("preserve me")
        self.assert_rejected(self.launch())
        self.assertEqual(sentinel.read_text(), "preserve me")
        sentinel.unlink()
        run.rmdir()
        for target in (self.source, self.work / "missing"):
            with self.subTest(target=target):
                run.symlink_to(target, target_is_directory=True)
                self.assert_rejected(self.launch())
                self.assertTrue(run.is_symlink())
                run.unlink()

    def test_output_file_is_rejected(self):
        self.fake_claude()
        self.output.write_text("keep")
        self.assert_rejected(self.launch())
        self.assertEqual(self.output.read_text(), "keep")

    def test_run_ancestor_of_source_is_rejected(self):
        self.fake_claude()
        self.assert_rejected(self.launch(output=self.work.parent, name=self.work.name))
        self.assertEqual((self.source / "cv.txt").read_text(), "Fictional researcher.\n")

    def test_invalid_names_create_nothing_even_in_dry_run(self):
        for name in (".", "..", "../escape", "a/b", "a\\b", "line\nbreak"):
            with self.subTest(name=name):
                self.assert_rejected(self.launch("--dry-run", name=name))
                self.assertFalse(self.output.exists())

    def test_missing_option_values_have_usage_errors(self):
        for option in ("-s", "--source", "-o", "--output", "-n", "--name"):
            for extra in ((), ("",), ("--dry-run",)):
                with self.subTest(option=option, extra=extra):
                    result = self.launch(option, *extra)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn("requires a value", result.stderr)
                    self.assertFalse(self.output.exists())

    def test_relative_output_is_absolute_in_prompt_and_environment(self):
        self.tool("claude", 'printf "%s\\n" "$@" > "$NIW_TEST_MARKER"\n')
        result = self.launch(output="relative output", name="run with spaces")
        self.assertEqual(result.returncode, 0, result.stderr)
        run = self.work.resolve() / "relative output" / "run with spaces"
        self.assertTrue((run / "federal_documents").is_dir())
        self.assertIn("RUN_DIR=" + str(run), (run / ".niw-run.env").read_text())
        self.assertIn("RUN_DIR    = " + str(run), self.marker.read_text())
        self.assertFalse((self.project / "relative output").exists())

    def test_safe_output_symlink_resolves_to_absolute_target(self):
        self.fake_claude()
        target = self.work / "actual output"
        target.mkdir()
        self.output.symlink_to(target, target_is_directory=True)
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RUN_DIR=" + str(target.resolve() / "run"),
                      (target / "run/.niw-run.env").read_text())

    def test_required_skill_missing_fails_before_creating_output(self):
        self.fake_claude()
        skill = self.project / ".claude/skills/niw-national-importance-research/SKILL.md"
        skill.unlink()
        result = self.launch()
        self.assert_rejected(result)
        self.assertIn("missing required pipeline file", result.stderr)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.launch("--dry-run").returncode, 0)

    def test_nonexecutable_intake_fails_before_creating_output(self):
        self.fake_claude()
        scan = self.project / ".claude/skills/document-summary-arrangement/scripts/scan-collection.sh"
        scan.chmod(0o644)
        result = self.launch()
        self.assert_rejected(result)
        self.assertIn("not executable", result.stderr)
        self.assertFalse(self.output.exists())

    def test_python_missing_fails_clearly_without_writes(self):
        (self.bin / "python3").unlink()
        result = self.launch("--dry-run")
        self.assertEqual(result.returncode, 127)
        self.assertIn("python3 is required", result.stderr)
        self.assertFalse(self.output.exists())

    def test_control_characters_in_output_are_rejected_without_writes(self):
        output = self.work / "line\nbreak"
        result = self.launch("--dry-run", output=output)
        self.assert_rejected(result)
        self.assertFalse(output.exists())
        output.mkdir()
        self.output.symlink_to(output, target_is_directory=True)
        self.assert_rejected(self.launch("--dry-run"))
        self.assertEqual(list(output.iterdir()), [])

    def test_concurrent_reservation_has_only_one_winner(self):
        # Exercise exclusive mkdir directly, independently of model behavior.
        command = [str(self.bin / "python3"), str(self.project / "src/prepare-run.py"),
                   "--create", str(self.source), str(self.output), "run"]
        processes = [subprocess.Popen(command, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True) for _ in range(2)]
        try:
            for process in processes:
                process.communicate(timeout=10)
            self.assertEqual(sorted(p.returncode for p in processes), [0, 1])
            self.assertTrue((self.output / "run/federal_documents").is_dir())
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate()

    @known_defect
    def test_missing_deliverables_cannot_report_success(self):
        self.fake_claude()
        result = self.launch()
        self.assertTrue(self.marker.exists(), result.stderr)
        self.assertNotEqual(result.returncode, 0, "No deliverables, but successful exit")

    def locate(self, quote):
        # This fixture tests matching logic, not PDF parsing (covered separately).
        self.tool("pdfinfo", 'echo "Pages: 1"\n')
        self.tool("pdftotext", 'echo "one two three four five six seven eight nine ten eleven twelve real ending"\n')
        pdf = self.work / "mock.pdf"
        pdf.write_bytes(b"%PDF mocked extraction fixture\n")
        result = subprocess.run(
            [BASH, str(ROOT / ".claude/skills/niw-federal-evidence-harvester/scripts/pdf-locate-quote.sh"),
             str(pdf), quote], env=self.env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_full_quote_is_located(self):
        self.assertEqual(self.locate(
            "one two three four five six seven eight nine ten eleven twelve real ending"), "1")

    def test_unrelated_quote_is_not_found(self):
        self.assertEqual(self.locate("This phrase never appears in the source"), "not-found")

    @known_defect
    def test_altered_quote_tail_cannot_return_verified_page(self):
        result = self.locate(
            "one two three four five six seven eight nine ten eleven twelve fabricated ending")
        self.assertFalse(result.isdigit(), "Prefix-only match is indistinguishable from full match")


if __name__ == "__main__":
    unittest.main(verbosity=2)
