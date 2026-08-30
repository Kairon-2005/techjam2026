"""The submission audit preserves externally verified release facts."""
from __future__ import annotations

import json
import hashlib
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from lab import audit as A


class PostHoldoutInfluenceManifestTest(unittest.TestCase):
    BASE_AGENT = '''\
        """Agent with ambient configuration."""
        import json
        import os

        DEFAULTS = {"weight": 1}

        def _load_config(config=None):
            resolved = dict(DEFAULTS)
            raw = os.environ.get("TJ_CONFIG")
            if raw:
                resolved.update(json.loads(raw))
            if config:
                resolved.update(config)
            return resolved

        class Agent:
            def rank(self, value):
                return value * DEFAULTS["weight"]
        '''
    HARDENED_AGENT = '''\
        """Agent with explicit configuration."""

        DEFAULTS = {"weight": 1}

        def _load_config(config=None):
            resolved = dict(DEFAULTS)
            if config:
                resolved.update(config)
            return resolved

        class Agent:
            def rank(self, value):
                return value * DEFAULTS["weight"]
        '''
    DOCSTRING_AGENT = HARDENED_AGENT.replace(
        "Agent with explicit configuration.",
        "Agent with explicit configuration and no learned weights.",
    )

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._git("init", "-q")
        self._git("config", "user.name", "Audit Test")
        self._git("config", "user.email", "audit@example.invalid")
        self._write("starter/agent.py", self.BASE_AGENT)
        self._commit("Consume the synthetic holdout once")
        self.holdout = self._git("rev-parse", "HEAD").strip()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _git(self, *args: str, binary: bool = False):
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True,
            capture_output=True,
            text=not binary,
        ).stdout

    def _write(self, name: str, content: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content), encoding="utf-8")

    def _commit(self, subject: str) -> str:
        self._git("add", ".")
        self._git("commit", "-q", "-m", subject)
        return self._git("rev-parse", "HEAD").strip()

    def _entry(self, commit: str, classification: str, rationale: str) -> dict:
        parent = self._git("rev-parse", f"{commit}^").strip()
        files = self._git(
            "diff-tree", "--no-commit-id", "--name-only", "-r", commit
        ).splitlines()
        patch = self._git(
            "diff", "--no-ext-diff", "--full-index", "--binary",
            parent, commit, binary=True,
        )
        starter_patch = self._git(
            "diff", "--no-ext-diff", "--full-index", "--binary",
            parent, commit, "--", "starter/", binary=True,
        )
        return {
            "commit": commit,
            "parent": parent,
            "subject": self._git("show", "-s", "--format=%s", commit).strip(),
            "classification": classification,
            "rationale": rationale,
            "holdout_result_used": False,
            "ranking_changed": False,
            "files": files,
            "starter_files": [name for name in files if name.startswith("starter/")],
            "patch_sha256": hashlib.sha256(patch).hexdigest(),
            "starter_patch_sha256": hashlib.sha256(starter_patch).hexdigest(),
        }

    def _make_history(self) -> list[dict]:
        self._write("starter/agent.py", self.HARDENED_AGENT)
        self._write(
            "tests/test_agent.py",
            "def test_environment_cannot_change_bare_agent():\n    pass\n",
        )
        hardened = self._commit("Freeze bare agent config against environment")
        self._write("starter/agent.py", self.DOCSTRING_AGENT)
        wording = self._commit("Clarify model weight claim")
        return [
            self._entry(
                hardened,
                "environment_hardening",
                "Independent release review removed ambient configuration input.",
            ),
            self._entry(
                wording,
                "docstring_only",
                "Independent release review corrected module documentation.",
            ),
        ]

    def _commit_manifest(self, entries: list[dict]) -> tuple[Path, str]:
        manifest = {
            "schema_version": 1,
            "holdout_commit": self.holdout,
            "entries": entries,
        }
        path = self.root / "lab/post_holdout_starter_changes.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(manifest, indent=2) + "\n"
        path.write_text(payload, encoding="utf-8")
        self._commit("Declare post-holdout starter changes")
        return path, hashlib.sha256(payload.encode()).hexdigest()

    def test_exact_declared_non_ranking_history_passes(self) -> None:
        path, digest = self._commit_manifest(self._make_history())

        got = A.verify_post_holdout_influence(self.root, path, digest)

        self.assertTrue(got["ok"], got["evidence"])
        self.assertIn("2 declared", got["evidence"])
        self.assertIn("DEFAULTS and Agent unchanged", got["evidence"])

    def test_an_undeclared_starter_commit_fails(self) -> None:
        path, digest = self._commit_manifest(self._make_history())
        self._write("starter/extra.py", "VALUE = 1\n")
        commit = self._commit("Undeclared starter change")

        got = A.verify_post_holdout_influence(self.root, path, digest)

        self.assertFalse(got["ok"])
        self.assertIn(commit[:7], got["evidence"])
        self.assertIn("unlisted", got["evidence"])

    def test_a_tampered_manifest_fails_even_when_the_worktree_copy_is_valid_json(self) -> None:
        path, digest = self._commit_manifest(self._make_history())
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["entries"][0]["rationale"] = "Rewritten after review."
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        got = A.verify_post_holdout_influence(self.root, path, digest)

        self.assertFalse(got["ok"])
        self.assertIn("manifest bytes", got["evidence"])

    def test_a_committed_false_patch_identity_fails(self) -> None:
        entries = self._make_history()
        entries[0]["starter_patch_sha256"] = "0" * 64
        path, digest = self._commit_manifest(entries)

        got = A.verify_post_holdout_influence(self.root, path, digest)

        self.assertFalse(got["ok"])
        self.assertIn("starter patch sha256", got["evidence"])

    def test_docstring_classification_cannot_hide_a_ranking_change(self) -> None:
        entries = self._make_history()
        self._write("starter/agent.py", self.DOCSTRING_AGENT.replace(
            "return value * DEFAULTS", "return value + DEFAULTS"
        ))
        commit = self._commit("Call ranking change a docstring edit")
        entries.append(self._entry(
            commit,
            "docstring_only",
            "This declaration must not override the semantic check.",
        ))
        path, digest = self._commit_manifest(entries)

        got = A.verify_post_holdout_influence(self.root, path, digest)

        self.assertFalse(got["ok"])
        self.assertIn("not docstring-only", got["evidence"])


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
