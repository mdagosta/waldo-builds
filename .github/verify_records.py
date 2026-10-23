#!/usr/bin/env python3
"""
Stateless verifier for record pull requests from `waldo model join`.

Merges a pull request only if it adds nothing but signed records under an existing run's rounds/,
each record matches the file it is in, and its signature verifies against the run plan's key.
Every record pins the plan it was made under by plan_sha256. Claims reserve a slot before work: a slot's next attempt only once the last one expired unfilled,
one slot per identity per unit, never dated ahead of this verifier's clock. Submissions and merges
are merged only from identities holding a claim for them on main.
It reads the PR's files as data and never runs its code. Pull requests touching anything else
(plans, this verifier) are left for human review.

Usage: verify_records.py <pull request number>   (GH_TOKEN and GITHUB_REPOSITORY set, run from main)
"""
import datetime
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

SIGNING_NAMESPACE = "waldo-join"
RECORD = re.compile(r"^runs/([^/]+)/rounds/round-(\d{4})/(claims|merges|submissions)/([^/]+)\.yaml(\.sig)?$")
CLAIM_FILE = re.compile(r"^(.+)-([1-9]\d*)(?:\.([2-9]|[1-9]\d+))?$")
# A claim dated ahead of this clock would hold its slot past claim_minutes.
CLOCK_SKEW = datetime.timedelta(minutes=2)
# As join reads records: anything it would ignore must not land, or it would hold a slot join cannot see.
KINDS = {"claims": "waldo-join-claim", "submissions": "waldo-join-submission", "merges": "waldo-join-merge"}
MODEL_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
RFC3339 = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)")


def stage_of(plan, number):
    pretrain = int(plan["pretrain"]["rounds"])
    return "bootstrap" if number == 0 else "pretrain" if number <= pretrain else "posttrain"


def timestamp(value):
    """An RFC 3339 time as join parses it, or None."""
    if not RFC3339.fullmatch(str(value)):
        return None
    return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def parse_scalar(text):
    """The small YAML subset plans use: scalars, flow lists and maps, and nested blocks."""
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
        run, round_text, kind, stem, _ = match.groups()
        plan_path = pathlib.Path("runs") / run / "plan.yaml"
        if not plan_path.exists():
            found.append(f"{name}: no run {run} on main")
            continue
        plan = parse_plan(plan_path.read_text())
        number_in_path = int(round_text)
        round_dir = plan_path.parent / "rounds" / f"round-{round_text}"
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
        rounds = 1 + int(plan["pretrain"]["rounds"]) + int((plan.get("posttrain") or {}).get("rounds", 0))
        units = ["all"] if number_in_path == 0 else [str(unit) for unit in plan["units"]]
        if not key:
            found.append(f"{name}: {identity} is not in {plan_path}'s identities")
            continue
        if record.get("run") != plan["name"] or record.get("round") != number_in_path or number_in_path >= rounds:
            found.append(f"{name}: run or round does not match its path and plan")
            continue
        # Records count only under the exact plan they were made under.
        if record.get("plan_sha256") != hashlib.sha256(plan_path.read_bytes()).hexdigest():
            found.append(f"{name}: plan_sha256 is not the sha256 of {plan_path} on main")
        if record.get("kind") != KINDS[kind] or record.get("schema") != 1 or record.get("stage") != stage_of(plan, number_in_path):
            found.append(f"{name}: kind, schema, or stage is not {KINDS[kind]}, 1, {stage_of(plan, number_in_path)}")
        if kind == "claims":
            found += claim_problems(name, record, stem, plan, units, round_dir)
        else:
            unit = record.get("unit") if kind == "submissions" else "close"
            if kind == "submissions" and (stem != f"{unit}-{identity}" or unit not in units):
                found.append(f"{name}: unit or identity does not match its file name and plan")
            if kind == "merges" and stem != identity:
                found.append(f"{name}: identity does not match its file name")
            if kind == "merges":
                closed = timestamp(record.get("closed_at"))
                if closed is None or closed > datetime.datetime.now(datetime.timezone.utc) + CLOCK_SKEW:
                    found.append(f"{name}: closed_at is not an RFC 3339 time at or before the verifier's clock")
                accepted = record.get("accepted")
                if not isinstance(accepted, list) or not all(isinstance(a, dict) and a.get("unit") in units and
                                                                MODEL_NAME.fullmatch(str(a.get("model"))) for a in accepted):
                    found.append(f"{name}: accepted units must be the round's units with plain model names")
            if not MODEL_NAME.fullmatch(str(record.get("model"))):
                found.append(f"{name}: model is not a plain model name")
            held = [slot for slot in held_slots(round_dir, identity) if slot_unit(slot) == unit]
            if not held:
                found.append(f"{name}: {identity} holds no claim on {unit} on main")
            elif kind == "submissions" and any(other != identity and (round_dir / "submissions" / f"{unit}-{other}.yaml").exists()
                                               for other in holders_of(round_dir, held[0])):
                # A slot is filled once: a late record from an expired holder would change what closers judge.
                found.append(f"{name}: {held[0]} was already filled by the identity that retook it")
            found += published_problems(name, record)
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


def published_problems(name, record):
    """A submission or merge pins every published file and a fixed Hugging Face revision."""
    found = []
    if not re.fullmatch(r"[0-9a-f]{64}", str(record.get("model_sha256"))):
        found.append(f"{name}: model_sha256 is not a sha256")
    files = record.get("files")
    if not isinstance(files, dict) or files.get("model.safetensors") != record.get("model_sha256") or not all(
            re.fullmatch(r"[\w-][\w.-]*", str(file)) and re.fullmatch(r"[0-9a-f]{64}", str(digest)) for file, digest in files.items()):
        found.append(f"{name}: files must map each published file name to its sha256, model.safetensors included")
    if not re.fullmatch(r"huggingface://[\w.-]+/[\w.-]+@[0-9a-f]{40}", str(record.get("url"))):
        found.append(f"{name}: url is not a pinned huggingface://user/model@commit")
    return found


def slot_unit(slot):
    return slot.rsplit("-", 1)[0]


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def held_slots(round_dir, identity):
    """The slots identity has claimed in this round on main, by any attempt."""
    return {str(claim.get("slot")) for claim in map(read_json, sorted((round_dir / "claims").glob("*.yaml")))
            if claim.get("identity") == identity}


def holders_of(round_dir, slot):
    """Every identity that has claimed slot in this round on main, by any attempt."""
    return [claim.get("identity") for claim in map(read_json, sorted((round_dir / "claims").glob("*.yaml"))) if claim.get("slot") == slot]


def filled(round_dir, slot, holders):
    """Whether any holder of slot has landed its submission or merge on main."""
    unit = slot_unit(slot)
    return any((round_dir / "merges" / f"{holder}.yaml").exists() if unit == "close" else
               (round_dir / "submissions" / f"{unit}-{holder}.yaml").exists() for holder in holders)


def full(round_dir, unit, replicas):
    """Whether unit has a submission from the holder of each of its replica slots on main."""
    holders = {claim.get("identity") for claim in map(read_json, sorted((round_dir / "claims").glob("*.yaml")))
               if str(claim.get("slot", "")).rsplit("-", 1)[0] == unit}
    return sum((round_dir / "submissions" / f"{unit}-{holder}.yaml").exists() for holder in holders) >= replicas


def claim_problems(name, record, stem, plan, units, round_dir):
    found = []
    match = CLAIM_FILE.match(stem)
    slot, attempt = str(record.get("slot")), record.get("attempt")
    if not match or not isinstance(attempt, int) or f"{match.group(1)}-{match.group(2)}" != slot or int(match.group(3) or 1) != attempt:
        return [f"{name}: slot or attempt does not match its file name"]
    unit, position = match.group(1), int(match.group(2))
    if unit != "close" and (unit not in units or position > int(plan.get("replicas", 1))):
        found.append(f"{name}: {slot} is not a slot of this round")
    if unit == "close" and not plan.get("round_minutes") and not all(full(round_dir, u, int(plan.get("replicas", 1))) for u in units):
        found.append(f"{name}: closing slots open once every unit has its replicas")
    identities = len(plan.get("identities") or {})
    closers = min(int(plan.get("closers", 3)), identities or int(plan.get("closers", 3)))
    if unit == "close" and position > closers:
        for earlier in range(1, position):
            if not filled(round_dir, f"close-{earlier}", holders_of(round_dir, f"close-{earlier}")):
                found.append(f"{name}: {slot} opens only once close-1 to close-{position - 1} have merges")
                break
    created, now = timestamp(record.get("created_at")), datetime.datetime.now(datetime.timezone.utc)
    if created is None:
        return found + [f"{name}: created_at is not an RFC 3339 time"]
    if created > now + CLOCK_SKEW:
        found.append(f"{name}: created_at is ahead of the verifier's clock")
    identity = record.get("identity")
    if (round_dir / "claims" / f"{stem}.yaml").exists():
        found.append(f"{name}: {slot} attempt {attempt} is already claimed")
    if any(slot_unit(held) == unit for held in held_slots(round_dir, identity)):
        found.append(f"{name}: {identity} already holds a claim on {unit} in this round")
    if attempt > 1:
        previous = round_dir / "claims" / (f"{slot}.yaml" if attempt == 2 else f"{slot}.{attempt - 1}.yaml")
        last = read_json(previous)
        if not previous.exists():
            found.append(f"{name}: attempt {attempt - 1} of {slot} is not on main")
        elif filled(round_dir, slot, holders_of(round_dir, slot)):
            found.append(f"{name}: {slot} is already filled")
        else:
            try:
                expires = datetime.datetime.fromisoformat(str(last.get("created_at"))) + datetime.timedelta(minutes=float(plan.get("claim_minutes", 60)))
                if now <= expires:
                    found.append(f"{name}: attempt {attempt - 1} of {slot} holds it until {expires.isoformat()}")
            except ValueError:
                pass
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
    # Rebase keeps each contributor's signed-off commit, with them as author. Two claims on one
    # slot conflict here, so the first merged stands.
    # --match-head-commit: a push after these checks must not be merged unchecked.
    head = subprocess.run(["git", "rev-parse", "FETCH_HEAD"], stdout=subprocess.PIPE, text=True, check=True).stdout.strip()
    merged = subprocess.run(["gh", "pr", "merge", number, "--repo", repository, "--rebase", "--match-head-commit", head])
    if merged.returncode:
        gh("pr", "close", number, "--repo", repository, "--comment", "Verified, but could not be merged onto main "
           "(most likely another record for the same file merged first).")


if __name__ == "__main__":
    main()
