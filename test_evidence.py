"""Public behavior tests for the v1 evidence contract."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from evidence import (EvidenceError, assess_reuse, build_manifest, manifest_digest,
                      validate_checker_report, validate_envelope)


def envelope(cases, *, status="pass", execution_status="completed", **overrides):
    value = {
        "schema_version": 1,
        "check_id": "harness-static",
        "run_id": "run-1",
        "job_id": "job-1",
        "adapter": {"id": "harness-check/v1", "version": 1,
                    "source_digest": "a" * 64},
        "command_ref": "b" * 64,
        "acceptance_ref": "c" * 64,
        "artifact_ref": "d" * 64,
        "environment_ref": "e" * 64,
        "execution_status": execution_status,
        "exit_code": 0,
        "status": status,
        "tests": {"discovered": len(cases), "required": [case["id"] for case in cases],
                  "cases": cases, "failures": 0, "errors": 0, "skipped": 0,
                  "expected_failures": 0, "unexpected_successes": 0,
                  "coverage": "complete"},
        "evidence_refs": [], "duration_ms": 0, "uncertainty": [],
    }
    value.update(overrides)
    return value


class EnvelopeTests(unittest.TestCase):
    def test_boolean_schema_version_is_not_version_one(self):
        value = envelope([{"id": "static.overall", "outcome": "pass", "code": "static.overall"}])
        value["schema_version"] = True
        with self.assertRaises(EvidenceError):
            validate_envelope(value, required_cases=["static.overall"])

    def test_boolean_nested_adapter_version_and_malformed_enums_are_rejected(self):
        cases = [{"id": "static.overall", "outcome": "pass", "code": "static.overall"}]
        for mutation in (lambda value: value["adapter"].update(version=True),
                         lambda value: value.update(execution_status=[]),
                         lambda value: value.update(status={}),
                         lambda value: value["tests"].update(coverage=[]),
                         lambda value: value["tests"]["cases"][0].update(outcome=[])):
            with self.subTest(mutation=mutation):
                value = envelope(list(cases)); mutation(value)
                with self.assertRaises(EvidenceError):
                    validate_envelope(value, required_cases=["static.overall"])
        with self.assertRaises(EvidenceError):
            validate_envelope(envelope(cases), required_cases=[{}])

    def test_known_required_failure_cannot_be_false_pass_when_wrapper_exits_zero(self):
        value = envelope([{"id": "static.overall", "outcome": "fail", "code": "static.overall"}])
        with self.assertRaises(EvidenceError) as raised:
            validate_envelope(value, required_cases=["static.overall"])
        self.assertEqual(raised.exception.code, "OUTPUT_INVALID")

    def test_exact_cases_and_closed_shapes_reject_malformed_duplicates_missing_and_extra(self):
        cases = [{"id": "static.overall", "outcome": "pass", "code": "static.overall"}]
        for mutation, code in (
            (lambda v: v.update(extra=True), "OUTPUT_INVALID"),
            (lambda v: v["tests"].update(cases=cases * 2), "DUPLICATE_CASE"),
            (lambda v: v["tests"].update(cases=[]), "MISSING_REQUIRED"),
            (lambda v: v["tests"].update(cases=cases + [{"id": "other.case", "outcome": "pass", "code": "other.case"}]), "OUTPUT_INVALID"),
        ):
            with self.subTest(code=code):
                value = envelope(list(cases)); mutation(value)
                with self.assertRaises(EvidenceError) as raised:
                    validate_envelope(value, required_cases=["static.overall"])
                self.assertEqual(raised.exception.code, code)

    def test_blockers_and_failure_precedence(self):
        cases = [{"id": "static.overall", "outcome": "pass", "code": "static.overall"}]
        value = envelope(cases, status="blocked", execution_status="timed_out")
        self.assertIs(validate_envelope(value, required_cases=["static.overall"]), value)
        failed = [{"id": "static.overall", "outcome": "fail", "code": "static.overall"}]
        value = envelope(failed, status="fail", execution_status="timed_out", exit_code=None,
                         uncertainty=["CLEANUP_PENDING"])
        value["tests"]["failures"] = 1
        self.assertIs(validate_envelope(value, required_cases=["static.overall"]), value)
        zero = envelope([], status="blocked")
        zero["tests"]["required"] = ["static.overall"]
        with self.assertRaises(EvidenceError): validate_envelope(zero, required_cases=["static.overall"])

    def test_null_tests_and_discovery_and_codes_remain_closed(self):
        for execution in ("setup_failed", "parser_failed", "timed_out"):
            with self.subTest(execution=execution):
                value = envelope([], status="blocked", execution_status=execution, tests=None, exit_code=None)
                self.assertIs(validate_envelope(value, required_cases=["static.overall"]), value)
        value = envelope([], status="pass", tests=None)
        with self.assertRaises(EvidenceError): validate_envelope(value, required_cases=[])
        value = envelope([{"id": "static.overall", "outcome": "pass", "code": "made.up"}])
        with self.assertRaises(EvidenceError): validate_envelope(value, required_cases=["static.overall"])
        value = envelope([{"id": "static.overall", "outcome": "pass", "code": "static.overall"}])
        value["tests"]["discovered"] = 2
        with self.assertRaises(EvidenceError): validate_envelope(value, required_cases=["static.overall"])


class CheckerTests(unittest.TestCase):
    nonce = "a" * 32

    def report(self, ref="artifact.txt", *, status="pass"):
        return {"schema_version": 1, "adapter": "harness-check/v1", "run_nonce": self.nonce,
                "requested_scope": ["static"], "execution_status": "completed", "status": status,
                "cases": [{"id": "static.overall", "outcome": "pass", "code": "static.overall", "evidence_refs": [ref]}],
                "evidence_refs": [], "uncertainty": []}

    def test_artifact_reference_rejects_traversal_and_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / "artifact.txt").write_text("ok")
            self.assertEqual(validate_checker_report(self.report(), required_cases=["static.overall"], run_nonce=self.nonce, requested_scope=["static"], artifacts_dir=root)["cases"][0]["id"], "static.overall")
            for ref in ("../outside", "/absolute", "a\\b"):
                with self.subTest(ref=ref):
                    with self.assertRaises(EvidenceError): validate_checker_report(self.report(ref), required_cases=["static.overall"], run_nonce=self.nonce, requested_scope=["static"], artifacts_dir=root)
            os.symlink("artifact.txt", root / "link")
            with self.assertRaises(EvidenceError): validate_checker_report(self.report("link"), required_cases=["static.overall"], run_nonce=self.nonce, requested_scope=["static"], artifacts_dir=root)

    def test_nonce_and_case_binding_are_closed(self):
        value = self.report(); value["run_nonce"] = "b" * 32
        with self.assertRaises(EvidenceError): validate_checker_report(value, required_cases=["static.overall"], run_nonce=self.nonce, requested_scope=["static"])

    def test_empty_scopes_and_malformed_version_or_enums_are_rejected(self):
        for required_cases, scope in (([], []), (["static.overall"], []), ([], ["static"])):
            with self.subTest(required_cases=required_cases, scope=scope):
                with self.assertRaises(EvidenceError):
                    validate_checker_report(self.report(), required_cases=required_cases,
                                            run_nonce=self.nonce, requested_scope=scope)
        with self.assertRaises(EvidenceError):
            validate_checker_report(self.report(), required_cases=[{}], run_nonce=self.nonce,
                                    requested_scope=["static"])
        for mutation in (lambda report: report.update(schema_version=True),
                         lambda report: report.update(execution_status=[]),
                         lambda report: report.update(status={}),
                         lambda report: report["cases"][0].update(outcome=[]),
                         lambda report: report.update(requested_scope=[{}])):
            with self.subTest(mutation=mutation):
                report = self.report(); mutation(report)
                with self.assertRaises(EvidenceError):
                    validate_checker_report(report, required_cases=["static.overall"],
                                            run_nonce=self.nonce, requested_scope=["static"])

    def test_screenshot_requires_nonce_bound_nonempty_case_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); ref = f"browser-{self.nonce}.png"
            report = {"schema_version": 1, "adapter": "harness-check/v1", "run_nonce": self.nonce,
                      "requested_scope": ["chrome-devtools"], "execution_status": "completed", "status": "pass",
                      "cases": [{"id": "chrome.screenshot", "outcome": "pass", "code": "chrome.screenshot", "evidence_refs": [ref]}], "evidence_refs": [], "uncertainty": []}
            with self.assertRaises(EvidenceError): validate_checker_report(report, required_cases=["chrome.screenshot"], run_nonce=self.nonce, requested_scope=["chrome-devtools"], artifacts_dir=root)
            (root / ref).write_bytes(b"png")
            self.assertIs(validate_checker_report(report, required_cases=["chrome.screenshot"], run_nonce=self.nonce, requested_scope=["chrome-devtools"], artifacts_dir=root), report)
            report["cases"][0]["evidence_refs"] = ["screenshot.png"]
            with self.assertRaises(EvidenceError): validate_checker_report(report, required_cases=["chrome.screenshot"], run_nonce=self.nonce, requested_scope=["chrome-devtools"], artifacts_dir=root)

    def test_screenshot_nonce_and_content_must_be_the_same_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); nonce_ref = f"empty-{self.nonce}.png"; other_ref = "content.png"
            (root / nonce_ref).touch(); (root / other_ref).write_bytes(b"png")
            report = {"schema_version": 1, "adapter": "harness-check/v1", "run_nonce": self.nonce, "requested_scope": ["chrome-devtools"], "execution_status": "completed", "status": "pass", "cases": [{"id": "chrome.screenshot", "outcome": "pass", "code": "chrome.screenshot", "evidence_refs": [nonce_ref, other_ref]}], "evidence_refs": [], "uncertainty": []}
            with self.assertRaises(EvidenceError): validate_checker_report(report, required_cases=["chrome.screenshot"], run_nonce=self.nonce, requested_scope=["chrome-devtools"], artifacts_dir=root)


class ManifestTests(unittest.TestCase):
    def build(self, root, names, **changes):
        values = {"coverage": "complete", "acceptance": {"sha256": "a" * 64},
                  "adapter": {"id": "harness-check/v1", "version": 1,
                              "source_digest": "b" * 64},
                  "policy": {"files": [{"path": "policy.md", "sha256": "c" * 64}]},
                  "environment": {"sha256": "d" * 64}}
        values.update(changes)
        return build_manifest(root, names, **values)

    def test_deterministic_content_mode_new_and_deleted_inputs_change_reuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); file = root / "input.py"; file.write_text("one")
            first = self.build(root, ["input.py"])
            self.assertEqual(manifest_digest(first), manifest_digest(self.build(root, ["input.py"])))
            file.write_text("two"); changed = self.build(root, ["input.py"])
            self.assertEqual(assess_reuse(first, changed)["verdict"], "mismatch")
            os.chmod(file, 0o700); self.assertEqual(assess_reuse(changed, self.build(root, ["input.py"]))["verdict"], "mismatch")
            (root / "new.py").write_text("new")
            added = self.build(root, ["input.py", "new.py"])
            self.assertEqual(assess_reuse(first, added)["verdict"], "mismatch")
            file.unlink(); deleted = self.build(root, ["input.py", "new.py"])
            self.assertEqual(deleted["coverage"], "incomplete")
            self.assertEqual(assess_reuse(added, deleted)["verdict"], "mismatch")

    def test_manifest_has_closed_coverage_rule_and_rejects_duplicate_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / "input.py").write_text("one")
            manifest = self.build(root, ["input.py"])
            self.assertEqual(set(manifest), {"schema_version", "coverage", "coverage_rule", "entries", "acceptance", "adapter", "policy", "environment"})
            self.assertEqual(manifest["coverage_rule"], "complete")
            with self.assertRaises(EvidenceError): self.build(root, ["input.py", "input.py"])
            with self.assertRaises(EvidenceError): self.build(root, [])

    def test_boolean_manifest_versions_and_empty_entries_cannot_reuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / "input.py").write_text("one")
            complete = self.build(root, ["input.py"])
            for key, value in (("schema_version", True),):
                with self.subTest(key=key):
                    forged = dict(complete); forged[key] = value
                    self.assertEqual(assess_reuse(forged, complete)["verdict"], "unknown")
            forged = dict(complete); forged["adapter"] = dict(complete["adapter"], version=True)
            self.assertEqual(assess_reuse(forged, complete)["verdict"], "unknown")
            forged = dict(complete); forged["entries"] = []
            self.assertEqual(assess_reuse(forged, complete)["verdict"], "unknown")

    def test_forged_complete_missing_entry_cannot_match(self):
        manifest = {"schema_version": 1, "coverage": "complete", "coverage_rule": "rule",
                    "entries": [{"path": "gone.py", "type": "missing"}], "acceptance": {},
                    "adapter": {}, "policy": {}, "environment": {}}
        self.assertEqual(assess_reuse(manifest, manifest)["verdict"], "unknown")

    def test_nested_json_boolean_and_integer_cannot_reuse_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / "input.py").write_text("ok")
            previous = self.build(root, ["input.py"])
            previous["environment"] = {"sha256": 1}
            current = dict(previous)
            current["environment"] = {"sha256": True}
            self.assertEqual(assess_reuse(previous, current)["verdict"], "unknown")

    def test_load_bearing_metadata_environment_and_unsafe_paths_do_not_reuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / "input.py").write_text("ok")
            first = self.build(root, ["input.py"])
            for changed in ({"acceptance": {"sha256": "e" * 64}}, {"adapter": {"id": "harness-check/v1", "version": 1, "source_digest": "e" * 64}}, {"policy": {"files": [{"path": "policy.md", "sha256": "e" * 64}]}}, {"environment": {"sha256": "e" * 64}}):
                with self.subTest(changed=changed): self.assertEqual(assess_reuse(first, self.build(root, ["input.py"], **changed))["verdict"], "mismatch")
            os.symlink("/etc/passwd", root / "escape")
            unsafe = self.build(root, ["escape"])
            self.assertEqual(unsafe["coverage"], "incomplete")
            self.assertEqual(assess_reuse(first, unsafe)["verdict"], "mismatch")
            self.assertEqual(assess_reuse(first, {"coverage": "complete"})["verdict"], "unknown")

    def test_manifest_closure_symlinks_metadata_and_known_incomplete_differences(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / "target.py").write_text("target")
            os.symlink("target.py", root / "inside")
            complete = self.build(root, ["inside", "target.py"])
            self.assertEqual(complete["coverage"], "complete")
            self.assertEqual(complete["entries"][0]["target"], "target.py")
            undeclared = self.build(root, ["inside"])
            self.assertEqual(undeclared["coverage"], "incomplete")
            (root / "target.py").unlink(); dangling = self.build(root, ["inside", "target.py"])
            self.assertEqual(dangling["coverage"], "incomplete")
            self.assertEqual(assess_reuse(complete, dangling)["verdict"], "mismatch")
            with self.assertRaises(EvidenceError): self.build(root, ["bad/../path"])
            with self.assertRaises(EvidenceError): self.build(root, ["inside"], acceptance={"bad": {1}})
            v2 = dict(complete); v2["schema_version"] = 2
            extra = dict(complete); extra["future"] = True
            self.assertEqual(assess_reuse(v2, complete)["verdict"], "unknown")
            self.assertEqual(assess_reuse(extra, complete)["verdict"], "unknown")
            changed_rule = self.build(root, ["inside", "target.py"], coverage="different rule")
            self.assertEqual(assess_reuse(dangling, changed_rule)["verdict"], "mismatch")

    def test_manifest_reuse_rejects_malformed_order_and_unsafe_symlink_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / "target.py").write_text("target")
            os.symlink("/outside", root / "escape")
            unsafe = self.build(root, ["escape"])
            self.assertEqual(unsafe["entries"][0], {"path": "escape", "type": "unsafe", "mode": unsafe["entries"][0]["mode"]})
            self.assertEqual(unsafe["coverage"], "incomplete")
            complete = self.build(root, ["target.py"])
            malformed = dict(complete); malformed["coverage"] = []
            self.assertEqual(assess_reuse(malformed, complete)["verdict"], "unknown")
            forged = dict(complete); forged["entries"] = [{"path": "b", "type": "file", "mode": 0o600, "digest": "a" * 64}, {"path": "a", "type": "file", "mode": 0o600, "digest": "b" * 64}]
            self.assertEqual(assess_reuse(forged, forged)["verdict"], "unknown")
            linked = self.build(root, ["target.py"])
            linked["entries"] = [{"path": "link", "type": "symlink", "mode": 0o777, "target": "target.py", "digest": "0" * 64}, linked["entries"][0]]
            self.assertEqual(assess_reuse(linked, linked)["verdict"], "unknown")

    def test_null_environment_is_unknown_unless_another_difference_is_known(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / "input.py").write_text("ok")
            known = self.build(root, ["input.py"])
            missing = self.build(root, ["input.py"], environment=None)
            self.assertEqual(assess_reuse(known, missing)["verdict"], "unknown")
            changed = self.build(root, ["input.py"], environment=None, policy={"files": [{"path": "policy.md", "sha256": "e" * 64}]})
            self.assertEqual(assess_reuse(known, changed)["verdict"], "mismatch")

    def test_complete_symlink_requires_a_declared_regular_file_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            os.symlink("right", root / "left")
            os.symlink("left", root / "right")
            manifest = self.build(root, ["left", "right"])
            self.assertEqual(manifest["coverage"], "incomplete")
            forged = self.build(root, ["left", "right"])
            forged["coverage"] = "complete"
            for entry in forged["entries"]:
                target = "right" if entry["path"] == "left" else "left"
                entry.update(type="symlink", target=target,
                             digest=hashlib.sha256(target.encode()).hexdigest())
            self.assertEqual(assess_reuse(forged, forged)["verdict"], "unknown")

    def test_empty_or_malformed_load_bearing_identities_cannot_reuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / "input.py").write_text("ok")
            manifest = self.build(root, ["input.py"])
            for key, value in (("acceptance", {}), ("adapter", {}), ("policy", {"files": []}),
                               ("environment", {})):
                with self.subTest(key=key):
                    with self.assertRaises(EvidenceError):
                        self.build(root, ["input.py"], **{key: value})
                    forged = dict(manifest); forged[key] = value
                    self.assertEqual(assess_reuse(forged, forged)["verdict"], "unknown")


if __name__ == "__main__":
    unittest.main()
