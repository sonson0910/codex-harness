"""Closed v1 evidence validation and declared-scope manifests."""
import hashlib
import json
import os
import stat
from pathlib import Path

_CODES = {"CHILD_EXIT", "CHILD_SIGNAL", "MISSING_REQUIRED", "DUPLICATE_CASE", "ZERO_TESTS", "LOAD_ERROR", "SETUP_ERROR", "UNKNOWN_ERROR_PHASE", "OUTPUT_INVALID", "BINDING_MISMATCH", "COVERAGE_INCOMPLETE", "CLEANUP_PENDING", "OWNER_UNKNOWN", "ARTIFACT_MISSING", "ARTIFACT_INVALID", "CONFIG_CHANGED", "EXECUTABLE_CHANGED"}
_STATUS = {"pass", "fail", "blocked"}
_EXECUTION = {"completed", "setup_failed", "timed_out", "cancelled", "parser_failed"}
_OUTCOMES = {"pass", "fail", "error", "skip", "expected_failure", "unexpected_success"}

class EvidenceError(ValueError):
    def __init__(self, code):
        self.code = code if code in _CODES else "OUTPUT_INVALID"
        super().__init__(self.code)

def _fail(code="OUTPUT_INVALID"): raise EvidenceError(code)
def _uint(v): return type(v) is int and v >= 0
def _string(v, n=256): return type(v) is str and 0 < len(v) <= n and "\x00" not in v
def _digest(v): return type(v) is str and len(v) == 64 and all(c in "0123456789abcdef" for c in v)
def _case_id(v): return _string(v) and v.isascii()
def _code(v, cases): return _case_id(v) and len(v) <= 64 and (v in _CODES or v in _STATUS or v in _EXECUTION or v in cases)
def _ref(v): return _string(v, 128) and not v.startswith("/") and "\\" not in v and all(x not in {"", ".", ".."} for x in v.split("/"))
def _keys(v, expected): return type(v) is dict and set(v) == set(expected)
def _list(v): return type(v) is list and len(v) <= 256
def _canonical_json(value): return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")
def _identities_valid(acceptance, adapter, policy, environment):
    files = policy.get("files") if type(policy) is dict else None
    return (_keys(acceptance, {"sha256"}) and _digest(acceptance["sha256"]) and
            _keys(adapter, {"id", "version", "source_digest"}) and
            _case_id(adapter["id"]) and type(adapter["version"]) is int and adapter["version"] == 1 and _digest(adapter["source_digest"]) and
            type(files) is list and 0 < len(files) <= 256 and
            all(_keys(item, {"path", "sha256"}) and _ref(item["path"]) and _digest(item["sha256"]) for item in files) and
            [item["path"] for item in files] == sorted(item["path"] for item in files) and
            len({item["path"] for item in files}) == len(files) and
            (environment is None or (_keys(environment, {"sha256"}) and _digest(environment["sha256"]))))

def validate_envelope(value, *, required_cases):
    """Validate a closed v1 runner envelope and its deterministic aggregate."""
    if type(required_cases) is not list or not _list(required_cases) or not all(_case_id(x) for x in required_cases) or len(set(required_cases)) != len(required_cases): _fail("MISSING_REQUIRED")
    fields = {"schema_version", "check_id", "run_id", "job_id", "adapter", "command_ref", "acceptance_ref", "artifact_ref", "environment_ref", "execution_status", "exit_code", "status", "tests", "evidence_refs", "duration_ms", "uncertainty"}
    if not _keys(value, fields) or type(value["schema_version"]) is not int or value["schema_version"] != 1 or not all(_string(value[x]) for x in ("check_id", "run_id", "job_id")): _fail()
    adapter = value["adapter"]
    if not _keys(adapter, {"id", "version", "source_digest"}) or not _string(adapter["id"]) or type(adapter["version"]) is not int or adapter["version"] != 1 or not _digest(adapter["source_digest"]): _fail()
    if not all(_digest(value[x]) or _ref(value[x]) for x in ("command_ref", "acceptance_ref", "artifact_ref", "environment_ref")): _fail()
    if type(value["execution_status"]) is not str or value["execution_status"] not in _EXECUTION or type(value["status"]) is not str or value["status"] not in _STATUS or not _uint(value["duration_ms"]) or (value["exit_code"] is not None and not _uint(value["exit_code"])): _fail()
    if not _list(value["evidence_refs"]) or not all(_ref(x) for x in value["evidence_refs"]) or not _list(value["uncertainty"]) or not all(_code(x, required_cases) for x in value["uncertainty"]): _fail()
    tests = value["tests"]
    if tests is None:
        if value["status"] != "blocked" or value["execution_status"] == "completed": _fail()
        return value
    wanted = {"discovered", "required", "cases", "failures", "errors", "skipped", "expected_failures", "unexpected_successes", "coverage"}
    if not _keys(tests, wanted) or not all(_uint(tests[x]) for x in wanted - {"required", "cases", "coverage"}) or type(tests["coverage"]) is not str or tests["coverage"] not in {"complete", "incomplete"} or not _list(tests["required"]) or not _list(tests["cases"]): _fail()
    if tests["required"] != required_cases or len(set(tests["required"])) != len(tests["required"]): _fail("BINDING_MISMATCH")
    seen, outcomes = set(), []
    for case in tests["cases"]:
        if not _keys(case, {"id", "outcome", "code"}) or not _case_id(case["id"]) or type(case["outcome"]) is not str or case["outcome"] not in _OUTCOMES or not _code(case["code"], required_cases): _fail()
        if case["id"] in seen: _fail("DUPLICATE_CASE")
        seen.add(case["id"]); outcomes.append(case)
    if seen != set(required_cases): _fail("MISSING_REQUIRED")
    if tests["discovered"] != len(outcomes): _fail()
    counts = {"fail": "failures", "error": "errors", "skip": "skipped", "expected_failure": "expected_failures", "unexpected_success": "unexpected_successes"}
    if any(tests[key] != sum(c["outcome"] == outcome for c in outcomes) for outcome, key in counts.items()): _fail()
    has_fail = any(c["outcome"] == "fail" for c in outcomes)
    blocker = tests["discovered"] == 0 or tests["coverage"] != "complete" or any(c["outcome"] != "pass" for c in outcomes) or bool(value["uncertainty"]) or value["execution_status"] != "completed" or value["exit_code"] != 0
    expected = "fail" if has_fail else ("blocked" if blocker else "pass")
    if value["status"] != expected: _fail()
    return value

def validate_checker_report(value, *, required_cases, run_nonce, requested_scope, artifacts_dir=None):
    """Validate closed checker cases-v1 output and retained artifact references."""
    fields = {"schema_version", "adapter", "run_nonce", "requested_scope", "execution_status", "status", "cases", "evidence_refs", "uncertainty"}
    if type(required_cases) is not list or not _list(required_cases) or not required_cases or not all(_case_id(case) for case in required_cases) or len(set(required_cases)) != len(required_cases) or type(requested_scope) is not list or not _list(requested_scope) or not requested_scope or not all(type(scope) is str and scope in {"static", "chrome-devtools", "serena"} for scope in requested_scope) or len(set(requested_scope)) != len(requested_scope) or not _keys(value, fields) or type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["adapter"] != "harness-check/v1" or value["run_nonce"] != run_nonce or not (type(run_nonce) is str and len(run_nonce) == 32 and all(c in "0123456789abcdef" for c in run_nonce)): _fail()
    if type(value["requested_scope"]) is not list or value["requested_scope"] != requested_scope: _fail("BINDING_MISMATCH")
    if type(value["execution_status"]) is not str or value["execution_status"] not in _EXECUTION or type(value["status"]) is not str or value["status"] not in _STATUS or not _list(value["cases"]) or not _list(value["evidence_refs"]) or not _list(value["uncertainty"]): _fail()
    refs, seen, outcomes = list(value["evidence_refs"]), set(), []
    for case in value["cases"]:
        if not _keys(case, {"id", "outcome", "code", "evidence_refs"}) or not _case_id(case["id"]) or type(case["outcome"]) is not str or case["outcome"] not in _STATUS or not _code(case["code"], required_cases) or not _list(case["evidence_refs"]): _fail()
        if case["id"] in seen: _fail("DUPLICATE_CASE")
        seen.add(case["id"]); outcomes.append(case["outcome"]); refs += case["evidence_refs"]
    if seen != set(required_cases) or len(seen) != len(required_cases): _fail("MISSING_REQUIRED")
    if not all(_ref(x) for x in refs) or not all(_code(x, required_cases) for x in value["uncertainty"]): _fail()
    screenshot_refs = next((case["evidence_refs"] for case in value["cases"] if case["id"] == "chrome.screenshot" and case["outcome"] == "pass"), None)
    screenshot_refs = [ref for ref in screenshot_refs or [] if run_nonce in ref]
    if screenshot_refs == [] and any(case["id"] == "chrome.screenshot" and case["outcome"] == "pass" for case in value["cases"]): _fail("ARTIFACT_INVALID")
    if artifacts_dir is not None:
        root = Path(artifacts_dir)
        if root.is_symlink() or not root.is_dir(): _fail("ARTIFACT_INVALID")
        for ref in refs:
            path = root
            try:
                for component in ref.split("/"):
                    path /= component
                    if path != root / ref and stat.S_ISLNK(path.lstat().st_mode): _fail("ARTIFACT_INVALID")
                info = path.lstat()
            except OSError: _fail("ARTIFACT_MISSING")
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: _fail("ARTIFACT_INVALID")
        if screenshot_refs and not any((root / ref).lstat().st_size > 0 for ref in screenshot_refs): _fail("ARTIFACT_INVALID")
    expected = "fail" if "fail" in outcomes else ("blocked" if value["execution_status"] != "completed" or "blocked" in outcomes or value["uncertainty"] else "pass")
    if value["status"] != expected: _fail()
    return value

def build_manifest(input_base, input_paths, *, coverage, acceptance, adapter, policy, environment=None):
    """Build a deterministic manifest without following unsafe declared links."""
    base = Path(input_base)
    if not base.is_absolute() or base.is_symlink() or not base.is_dir() or type(input_paths) is not list or not _list(input_paths) or not input_paths or not all(_ref(name) for name in input_paths) or len(set(input_paths)) != len(input_paths) or not _string(coverage, 1024): _fail("COVERAGE_INCOMPLETE")
    try: _canonical_json((acceptance, adapter, policy, environment))
    except (TypeError, ValueError): _fail()
    if not _identities_valid(acceptance, adapter, policy, environment): _fail()
    entries, complete = [], True
    declared = set(input_paths)
    for name in input_paths:
        valid = _ref(name); path = base / name if valid else base
        try:
            if valid:
                parent = base
                for component in name.split("/")[:-1]:
                    parent /= component
                    if stat.S_ISLNK(parent.lstat().st_mode): valid = False; break
            info = path.lstat() if valid else None
        except OSError: info = None
        if not valid or info is None: entry = {"path": name, "type": "missing"}; complete = False
        elif stat.S_ISREG(info.st_mode):
            try:
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    while chunk := handle.read(64 * 1024): digest.update(chunk)
                entry = {"path": name, "type": "file", "mode": info.st_mode & 0o7777, "digest": digest.hexdigest()}
            except OSError: entry = {"path": name, "type": "unreadable", "mode": info.st_mode & 0o7777}; complete = False
        elif stat.S_ISLNK(info.st_mode):
            try:
                raw_target = os.readlink(path)
                target_name = os.path.normpath(str(Path(name).parent / raw_target)).replace(os.sep, "/")
                target_path = base / target_name
                target_info = target_path.lstat()
                safe = _ref(raw_target) and _ref(target_name) and target_name in declared and stat.S_ISREG(target_info.st_mode)
            except OSError: safe = False
            if safe:
                entry = {"path": name, "type": "symlink", "mode": info.st_mode & 0o7777, "target": target_name, "digest": hashlib.sha256(target_name.encode()).hexdigest()}
            else:
                entry = {"path": name, "type": "unsafe", "mode": info.st_mode & 0o7777}; complete = False
        else: entry = {"path": name, "type": "special", "mode": info.st_mode & 0o7777}; complete = False
        entries.append(entry)
    return {"schema_version": 1, "coverage": "complete" if complete else "incomplete", "coverage_rule": coverage, "entries": sorted(entries, key=lambda x: x["path"]), "acceptance": acceptance, "adapter": adapter, "policy": policy, "environment": environment}

def manifest_digest(manifest):
    try: encoded = _canonical_json(manifest)
    except (TypeError, ValueError): _fail()
    return hashlib.sha256(encoded).hexdigest()

def assess_reuse(previous, current):
    required = {"schema_version", "coverage", "coverage_rule", "entries", "acceptance", "adapter", "policy", "environment"}
    def valid(manifest):
        if not _keys(manifest, required) or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1 or type(manifest["coverage"]) is not str or manifest["coverage"] not in {"complete", "incomplete"} or not _string(manifest["coverage_rule"], 1024) or not _list(manifest["entries"]) or not manifest["entries"]: return False
        if not _identities_valid(manifest["acceptance"], manifest["adapter"], manifest["policy"], manifest["environment"]): return False
        try: _canonical_json(manifest)
        except (TypeError, ValueError): return False
        paths, last_path, safe_entries = set(), None, True
        for entry in manifest["entries"]:
            if type(entry) is not dict or type(entry.get("path")) is not str or type(entry.get("type")) is not str or entry["type"] not in {"file", "symlink", "missing", "unreadable", "special", "unsafe"}: return False
            exact = {"file": {"path", "type", "mode", "digest"}, "symlink": {"path", "type", "mode", "target", "digest"}, "missing": {"path", "type"}, "unreadable": {"path", "type", "mode"}, "special": {"path", "type", "mode"}, "unsafe": {"path", "type", "mode"}}[entry["type"]]
            if set(entry) != exact or not _ref(entry["path"]) or entry["path"] in paths or (last_path is not None and entry["path"] <= last_path): return False
            paths.add(entry["path"])
            last_path = entry["path"]
            if entry["type"] in {"file", "symlink", "unreadable", "special", "unsafe"} and (not _uint(entry["mode"]) or entry["mode"] > 0o7777): return False
            if entry["type"] in {"file", "symlink"} and not _digest(entry["digest"]): return False
            if entry["type"] == "symlink" and (not _ref(entry["target"]) or entry["digest"] != hashlib.sha256(entry["target"].encode()).hexdigest()): return False
            safe_entries = safe_entries and entry["type"] in {"file", "symlink"}
        types = {entry["path"]: entry["type"] for entry in manifest["entries"]}
        if any(entry["type"] == "symlink" and types.get(entry["target"]) != "file" for entry in manifest["entries"]): return False
        return (manifest["coverage"] == "complete") == safe_entries
    if not valid(previous) or not valid(current): return {"verdict": "unknown", "reasons": ["COVERAGE_INCOMPLETE"]}
    reasons = []
    for key, code in (("entries", "CONFIG_CHANGED"), ("acceptance", "CONFIG_CHANGED"), ("adapter", "EXECUTABLE_CHANGED"), ("policy", "CONFIG_CHANGED"), ("coverage", "COVERAGE_INCOMPLETE"), ("coverage_rule", "CONFIG_CHANGED")):
        if _canonical_json(previous[key]) != _canonical_json(current[key]): reasons.append(code)
    if previous["environment"] is not None and current["environment"] is not None and _canonical_json(previous["environment"]) != _canonical_json(current["environment"]): reasons.append("CONFIG_CHANGED")
    if reasons: return {"verdict": "mismatch", "reasons": sorted(set(reasons))}
    if previous["coverage"] != "complete" or current["coverage"] != "complete" or previous["environment"] is None or current["environment"] is None: return {"verdict": "unknown", "reasons": ["COVERAGE_INCOMPLETE"]}
    return {"verdict": "match", "reasons": []}
