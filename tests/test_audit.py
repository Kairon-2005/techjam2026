"""The submission audit preserves externally verified release facts."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lab import audit as A


class ExternalStatusManifestTest(unittest.TestCase):
    def test_tree_clean_ignores_only_the_generated_checklist(self) -> None:
        checklist = " M docs/SUBMISSION_CHECKLIST.md"
        with mock.patch.object(A, "_sh", return_value=checklist):
            self.assertEqual({"ok": True, "evidence": "clean"},
                             A.check_tree_clean([]))

        with mock.patch.object(
            A, "_sh", return_value=checklist + "\n M lab/audit.py"
        ):
            self.assertEqual(
                {"ok": False, "evidence": [" M lab/audit.py"]},
                A.check_tree_clean([]),
            )

    def test_committed_facts_drive_external_rows_without_process_state(self) -> None:
        expected = [
            (
                "Public GitHub repository published",
                {
                    "ok": True,
                    "evidence": (
                        "release candidate `techjam-2026-final-rc2` is "
                        "published at https://github.com/Kairon-2005/techjam2026"
                    ),
                },
            ),
            (
                "Public demo video linked",
                {
                    "ok": False,
                    "evidence": "README still carries the demo-video placeholder",
                },
            ),
            (
                "Devpost submission live",
                {
                    "ok": False,
                    "evidence": (
                        "docs/DEVPOST_DRAFT.md is a DRAFT; nothing has been "
                        "submitted, so no Devpost URL exists yet"
                    ),
                },
            ),
            (
                "Linux portability CI has run",
                {
                    "ok": True,
                    "evidence": (
                        "[run 33290548542](https://github.com/Kairon-2005/"
                        "techjam2026/actions/runs/33290548542) succeeded on "
                        "Ubuntu for Python 3.10 and 3.11; both jobs ran the "
                        "full suite, reproduced TechnicalScore 0.932067 "
                        "exactly, and confirmed zero third-party imports"
                    ),
                },
            ),
        ]

        with mock.patch.object(
            A.subprocess,
            "run",
            side_effect=AssertionError("external checks must stay offline"),
        ):
            got = [(label, check([])) for label, check in A.EXTERNAL]

        self.assertEqual(expected, got)

    def test_loader_rejects_malformed_external_facts(self) -> None:
        valid = {
            "public_repository": {
                "complete": True,
                "release_tag": "techjam-2026-final-rc3",
                "url": (
                    "https://github.com/example/project/tree/"
                    "techjam-2026-final-rc3"
                ),
            },
            "linux_ci": {
                "complete": True,
                "url": "https://github.com/example/project/actions/runs/3",
            },
            "demo_video": {"complete": False, "url": None},
            "devpost": {"complete": False, "url": None},
        }
        malformed = [
            (
                "external status keys",
                {k: v for k, v in valid.items() if k != "devpost"},
            ),
            (
                "public_repository keys",
                {
                    **valid,
                    "public_repository": {
                        "complete": True,
                        "url": (
                            "https://github.com/example/project/tree/"
                            "techjam-2026-final-rc3"
                        ),
                    },
                },
            ),
            (
                "complete must be a boolean",
                {**valid, "demo_video": {"complete": 0, "url": None}},
            ),
            (
                "pending entries must have a null URL",
                {
                    **valid,
                    "demo_video": {
                        "complete": False,
                        "url": "https://youtu.be/example",
                    },
                },
            ),
            (
                "complete entries require an HTTPS URL",
                {**valid, "linux_ci": {"complete": True, "url": None}},
            ),
            (
                "complete entries require an HTTPS URL",
                {
                    **valid,
                    "linux_ci": {
                        "complete": True,
                        "url": "http://github.com/example/project/actions/runs/3",
                    },
                },
            ),
            (
                "release_tag must be a non-empty string",
                {
                    **valid,
                    "public_repository": {
                        "complete": True,
                        "release_tag": "",
                        "url": "https://github.com/example/project/tree/rc3",
                    },
                },
            ),
            (
                "public_repository.url must identify its release tag",
                {
                    **valid,
                    "public_repository": {
                        "complete": True,
                        "release_tag": "techjam-2026-final-rc3",
                        "url": "https://github.com/example/project",
                    },
                },
            ),
            (
                "public_repository.url must identify its release tag",
                {
                    **valid,
                    "public_repository": {
                        "complete": True,
                        "release_tag": "techjam-2026-final-rc3",
                        "url": "https://github.com/example/project/tree/rc2",
                    },
                },
            ),
            (
                "linux_ci.url must identify a numeric Actions run in the "
                "public repository",
                {
                    **valid,
                    "linux_ci": {
                        "complete": True,
                        "url": "https://github.com/other/project/actions/runs/3",
                    },
                },
            ),
            (
                "linux_ci cannot be complete while public_repository is pending",
                {
                    **valid,
                    "public_repository": {
                        "complete": False,
                        "release_tag": "techjam-2026-final-rc3",
                        "url": None,
                    },
                    "linux_ci": {
                        "complete": True,
                        "url": "https://ci.example/runs/3",
                    },
                },
            ),
        ]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "external_status.json"
            for message, payload in malformed:
                with self.subTest(message=message, payload=payload):
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, message):
                        A.load_external_status(path)

    def test_main_aborts_when_external_facts_are_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status = root / "external_status.json"
            status.write_text("{}", encoding="utf-8")
            with (
                mock.patch.object(A, "EXTERNAL_STATUS", status),
                mock.patch.object(A, "CHECKS", []),
                mock.patch.object(A, "tracked", return_value=[]),
                mock.patch.object(A, "OUT", root / "checklist.md"),
            ):
                with self.assertRaisesRegex(ValueError, "external status keys"):
                    A.main([])

    def test_main_describes_manifest_backed_completion_truthfully(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "checklist.md"
            with (
                mock.patch.object(A, "CHECKS", []),
                mock.patch.object(A, "tracked", return_value=[]),
                mock.patch.object(A, "OUT", out),
            ):
                self.assertEqual(0, A.main([]))

            generated = out.read_text(encoding="utf-8")
            self.assertIn(
                "Status is read from `lab/external_status.json`; complete rows "
                "carry public evidence URLs and pending rows carry null URLs.",
                generated,
            )


if __name__ == "__main__":
    unittest.main()
