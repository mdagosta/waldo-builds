#!/usr/bin/env python3
"""
Stateless verifier for record pull requests from `waldito join`.

Merges a pull request only if it adds nothing but signed records under an existing run's rounds/,
each record matches the file it is in, and its signature verifies against the run plan's key.
It reads the PR's files as data and never runs its code. Pull requests touching anything else
(plans, this verifier) are left for human review.

Usage: verify_records.py <pull request number>   (GH_TOKEN and GITHUB_REPOSITORY set, run from main)
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

SIGNING_NAMESPACE = "waldito"
RECORD = re.compile(r"^runs/([^/]+)/rounds/round-(\d{4})/(merge\.yaml|submissions/([^/]+)\.yaml)(\.sig)?$")


def parse_scalar(text):
    """Same YAML subset as waldito's parse_plan, so plans read the same here and in join."""
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        return [parse_scalar(item) for item in text[1:-1].split(",") if item.strip()]
    if text.startswith("{") and text.endswith("}"):
        pairs = (item.split(":", 1) for item in text[1:-1].split(",") if item.strip())
        return {key.strip(): parse_scalar(value) for key, value in pairs}
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            pass
    return {"true": True, "false": False, "null": None}.get(text, text)


def parse_plan(text):
    lines = []
    for raw in text.splitlines():
        line = "" if raw.lstrip().startswith("#") else raw.split(" #", 1)[0].rstrip()
        if line.strip():
            lines.append((len(line) - len(line.lstrip()), line.strip()))

    def block(index, indent):
        if lines[index][1].startswith("- "):
            items = []
            while index < len(lines) and lines[index][0] == indent and lines[index][1].startswith("- "):
                items.append(parse_scalar(lines[index][1][2:]))
                index += 1
            return items, index
        mapping = {}
        while index < len(lines) and lines[index][0] == indent:
            key, _, value = lines[index][1].partition(":")
            index += 1
            if value.strip():
                mapping[key.strip()] = parse_scalar(value)
            elif index < len(lines) and lines[index][0] > indent:
                mapping[key.strip()], index = block(index, lines[index][0])
            else:
                mapping[key.strip()] = None
        return mapping, index

    return block(0, lines[0][0])[0]


def gh(*args):
    return subprocess.run(["gh", *args], stdout=subprocess.PIPE, text=True, check=True).stdout


def key_of(entry):
    return entry if isinstance(entry, str) else (entry or {}).get("key")


def problems(number, repository):
    """Why this PR cannot be auto-merged, or [] if it can; None if it is not a record PR at all."""
    files = json.loads(gh("api", "--paginate", "--slurp", f"repos/{repository}/pulls/{number}/files"))
    files = [entry for page in files for entry in page]
    matches = {entry["filename"]: RECORD.match(entry["filename"]) for entry in files}
    if not any(matches.values()):
        return None
    found = [f"{name} is not a record file" for name, match in matches.items() if not match]
    found += [f"{entry['filename']} is {entry['status']}; records are only ever added" for entry in files if entry["status"] != "added"]
    commits = json.loads(gh("api", f"repos/{repository}/pulls/{number}"))["commits"]
    if commits != 1:
        found.append(f"{commits} commits; a record pull request is one commit")
    if found:
        return found
    subprocess.run(["git", "fetch", "-q", "origin", f"refs/pull/{number}/head"], check=True)
    for name, match in matches.items():
        if name.endswith(".sig"):
            if name[:-4] not in matches:
                found.append(f"{name} has no record beside it")
            continue
        run, round_text, _, submission, _ = match.groups()
        plan_path = pathlib.Path("runs") / run / "plan.yaml"
        if not plan_path.exists():
            found.append(f"{name}: no run {run} on main")
            continue
        plan = parse_plan(plan_path.read_text())
        number_in_path = int(round_text)
        if (plan_path.parent / "rounds" / f"round-{round_text}" / "merge.yaml").exists():
            found.append(f"{name}: round {number_in_path} is already merged")
        if name + ".sig" not in matches:
            found.append(f"{name} has no signature")
            continue
        text = subprocess.run(["git", "show", f"FETCH_HEAD:{name}"], stdout=subprocess.PIPE, check=True).stdout
        try:
            record = json.loads(text)
        except ValueError:
            found.append(f"{name} is not JSON-compatible YAML")
            continue
        identity = str(record.get("identity"))
        key = key_of((plan.get("identities") or {}).get(identity))
        rounds = 1 + int(plan["pretrain"]["rounds"]) + int(plan["posttrain"]["rounds"])
        units = ["all"] if number_in_path == 0 else [str(unit) for unit in plan["units"]]
        if not key:
            found.append(f"{name}: {identity} is not in {plan_path}'s identities")
            continue
        if record.get("run") != plan["name"] or record.get("round") != number_in_path or number_in_path >= rounds:
            found.append(f"{name}: run or round does not match its path and plan")
        if submission is not None and (submission != f"{record.get('unit')}-{identity}" or record.get("unit") not in units):
            found.append(f"{name}: unit or identity does not match its file name and plan")
        if not re.fullmatch(r"[0-9a-f]{64}", str(record.get("model_sha256"))):
            found.append(f"{name}: model_sha256 is not a sha256")
        files = record.get("files")
        if not isinstance(files, dict) or files.get("model.safetensors") != record.get("model_sha256") or not all(
                re.fullmatch(r"[\w-][\w.-]*", str(file)) and re.fullmatch(r"[0-9a-f]{64}", str(digest)) for file, digest in files.items()):
            found.append(f"{name}: files must map each published file name to its sha256, model.safetensors included")
        if not re.fullmatch(r"huggingface://[\w.-]+/[\w.-]+@[0-9a-f]{40}", str(record.get("url"))):
            found.append(f"{name}: url is not a pinned huggingface://user/model@commit")
        signature = subprocess.run(["git", "show", f"FETCH_HEAD:{name}.sig"], stdout=subprocess.PIPE, check=True).stdout
        with tempfile.TemporaryDirectory() as scratch:
            allowed, sig = pathlib.Path(scratch) / "allowed_signers", pathlib.Path(scratch) / "record.sig"
            allowed.write_text(f'{identity} namespaces="{SIGNING_NAMESPACE}" {key}\n')
            sig.write_bytes(signature)
            result = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(allowed), "-I", identity, "-n", SIGNING_NAMESPACE,
                                     "-s", str(sig)], input=text, capture_output=True)
            if result.returncode:
                found.append(f"{name}: signature does not verify as {identity}")
    return found


def main():
    number, repository = sys.argv[1], os.environ["GITHUB_REPOSITORY"]
    found = problems(number, repository)
    if found is None:
        print(f"#{number} is not a record pull request; leaving it for review")
        return
    if found:
        print("\n".join(found))
        gh("pr", "close", number, "--repo", repository, "--comment", "Not merged by the record verifier:\n\n" + "\n".join(f"- {item}" for item in found))
        return
    # Rebase keeps each contributor's signed-off commit, with them as author. Two merge.yaml for one
    # round conflict here, so the first merged stands.
    # --match-head-commit: a push after these checks must not be merged unchecked.
    head = subprocess.run(["git", "rev-parse", "FETCH_HEAD"], stdout=subprocess.PIPE, text=True, check=True).stdout.strip()
    merged = subprocess.run(["gh", "pr", "merge", number, "--repo", repository, "--rebase", "--match-head-commit", head])
    if merged.returncode:
        gh("pr", "close", number, "--repo", repository, "--comment", "Verified, but could not be merged onto main "
           "(most likely another record for the same file merged first).")


if __name__ == "__main__":
    main()
