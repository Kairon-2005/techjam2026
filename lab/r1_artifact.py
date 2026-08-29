"""Fetch the pinned cross-encoder artifact, and refuse anything else.

THE ONLY NETWORKED STEP IN THE PROJECT, and it is a PREPARATION step: the
shipped path never fetches (`docs/submission_rules.md` warns that scoring may
run with the network disabled), and `notes/40` requires the artifact to load
offline. This module exists so that "we downloaded the model" is a checkable
claim rather than a line of shell history.

Every file is requested at the PINNED REVISION SHA in the URL -- not at `main`,
which moves -- and every downloaded byte count is checked against the sizes
already committed in `lab/r0/artifacts/manifest.json`. A size that disagrees is
a different artifact, and the download is discarded rather than kept with a
note. The sha256 of what actually landed is written back so later phases can
check the bytes and not just their number.

    python3 -m lab.r1_artifact --model ms-marco-TinyBERT-L2-v2
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path("lab/r0/artifacts")
MANIFEST = ROOT / "manifest.json"
DIGESTS = ROOT / "digests.json"
# The manifest keys are FULL repo ids ("cross-encoder/ms-marco-TinyBERT-L2-v2").
# The directory on disk uses the bare name, which is what lab/r0/run_r0.sh and
# the `semantic_model_dir` config both point at.
BASE = "https://huggingface.co/{model}/resolve/{revision}/{name}"
DEFAULT_MODEL = "cross-encoder/ms-marco-TinyBERT-L2-v2"
TIMEOUT = 120


class ArtifactError(RuntimeError):
    """What arrived is not what the manifest pinned."""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(model: str, manifest_path: Path = MANIFEST, root: Path = ROOT) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if model not in manifest:
        raise ArtifactError(f"{model!r} is not pinned in {manifest_path}; "
                            f"pinned: {sorted(manifest)}")
    entry = manifest[model]
    revision = entry["revision"]
    target = root / model.split("/")[-1]
    target.mkdir(parents=True, exist_ok=True)
    digests: dict[str, str] = {}
    for name, expected_bytes in sorted(entry["files"].items()):
        out = target / name
        out.parent.mkdir(parents=True, exist_ok=True)
        url = BASE.format(model=model, revision=revision, name=name)
        if out.exists() and out.stat().st_size == expected_bytes:
            digests[name] = sha256_of(out)
            print(f"  have  {name}  {expected_bytes} bytes")
            continue
        print(f"  fetch {name}  <- {url}", flush=True)
        tmp = out.with_suffix(out.suffix + ".part")
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response, \
                tmp.open("wb") as fh:
            while chunk := response.read(1 << 20):
                fh.write(chunk)
        got = tmp.stat().st_size
        if got != expected_bytes:
            tmp.unlink(missing_ok=True)
            raise ArtifactError(
                f"{name}: {got} bytes, manifest pins {expected_bytes}. This is "
                f"a different artifact from the one Phase 7A-R0 measured; "
                f"discarded rather than kept.")
        tmp.replace(out)
        digests[name] = sha256_of(out)
    record = {"model": model, "revision": revision, "dir": str(target),
              "sha256": digests,
              "total_bytes": sum(entry["files"].values())}
    existing = json.loads(DIGESTS.read_text(encoding="utf-8")) if DIGESTS.exists() else {}
    # An artifact whose bytes changed under a pinned revision is a supply
    # problem, not a refresh. Recorded loudly rather than overwritten.
    previous = existing.get(model)
    if previous and previous.get("sha256") != digests:
        raise ArtifactError(
            f"{model} at revision {revision} now hashes differently than the "
            f"digests already recorded in {DIGESTS}. The pinned revision "
            f"served different bytes; nothing is overwritten.")
    existing[model] = record
    DIGESTS.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
    return record


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lab.r1_artifact")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args(argv)
    record = fetch(args.model)
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
