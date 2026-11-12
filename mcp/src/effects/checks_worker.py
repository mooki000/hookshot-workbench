"""Private installed CPU worker. Only checks submissions construct its request file."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from src.config import Config
from src.deps import Deps
from src.errors import WorkbenchError
from src.models import Build, Producer, State, utc_now
from src.models.checks import SourceResult
from src.effects.checks import (build_env, catalogue, checkout, copy_tree, donor_record, profile,
    seed_profile, snapshot, tree_identity, write_new)
from src.effects.checks_common import (MAX_BYTES, TEST, dry_run, file_hash, git, json_read,
    parse_pr, parse_source, parse_windows, prerequisite, safe_relative)


def dependencies(payload):
    raw = payload["config"]
    converted = {}
    for key, value in raw.items():
        if key.endswith("roots"):
            value = tuple(Path(p) for p in value)
        elif key in ("state_root", "harness_dir", "gpu_gate", "intel_gate", "build_gate", "checkout", "dependency_python", "checks_catalogue"):
            value = Path(value) if value is not None else None
        converted[key] = value
    deps = Deps(Config(**converted))
    deps.registry.open()
    return deps


class Worker:
    def __init__(self, deps, payload, folder):
        self.deps, self.payload, self.folder = deps, payload, folder
        self.job_id = payload["job_id"]
        self.request = payload["request"]
        self.operation = payload["operation"]
        self.cat = catalogue(deps)
        self.prof, _ = profile(deps, self.cat, self.request.get("profile_id", "default"))
        self.artifacts = []
        self.steps = 0
        self.phase = None

    def progress(self, phase, **data):
        if phase != self.phase:
            self.phase = phase
            self.deps.registry.update_job(self.job_id, phase=phase, completed_steps=self.steps, progress_text=phase)
        if data:
            self.deps.registry.append_event(self.job_id, "check_progress", data)

    def artifact(self, path, mime="text/plain", *, complete=True):
        artifact = self.deps.registry.add_artifact(path, guards=self.deps.paths, mime=mime,
            producer=Producer(tool=self.operation, source_commit=self.request["commit"]),
            run_id=self.payload["run_ids"][0] if self.payload["run_ids"] else None, complete=complete)
        self.artifacts.append(artifact)
        self.deps.registry.update_job(self.job_id, artifact_uris=[a.uri for a in self.artifacts])
        return artifact

    def stream(self, argv, source, env, label, *, build=False):
        path = self.folder / (label + ".log")
        fd = self.deps.paths.reserve_file(path)
        proc = subprocess.Popen([str(x) for x in argv], cwd=source, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        content = bytearray()
        partial = b""
        try:
            with os.fdopen(fd, "wb") as log:
                while chunk := proc.stdout.read1(65536):
                    content.extend(chunk)
                    log.write(chunk)
                    if len(content) > MAX_BYTES:
                        raise self.deps.error("backend_failed", "checks_output_limit")
                    partial += chunk
                    lines = partial.split(b"\n")
                    partial = lines.pop()
                    if len(partial) > 65536:
                        raise self.deps.error("backend_failed", "checks_line_limit")
                    for line in lines:
                        self.line(line.decode("utf-8", "replace"), build)
            code = proc.wait()
        finally:
            if proc.poll() is None:
                proc.terminate()
            proc.stdout.close()
        self.artifact(path, complete=True)
        return code, content.decode("utf-8", "replace")

    def line(self, line, build):
        if build:
            stage = re.match(r"^@@stage (read|lift|compile|finish)\b", line)
            if stage:
                self.progress({"read": "inputs"}.get(stage[1], stage[1]))
            elif re.fullmatch(r"compiled in \d+s, 0 failed", line) or line.startswith("linked "):
                self.progress("link")
            elif re.match(r"^@@plan \d+ \d+$", line):
                self.progress("compile")
            if re.match(r"^@@file \S+ [\d.]+ (?:new|cached|again)$", line):
                self.steps += 1
                self.deps.registry.update_job(self.job_id, completed_steps=self.steps)
        else:
            row = re.match(r"^(pass|FAIL|TIMEOUT)\s+[\d.]+s\s+(" + TEST + r")(?:\s|$)", line)
            if row:
                self.progress("tests", name=row[2], status=row[1])
            elif re.match(r"^(PASS|FAIL|UNJUDGED)\s+" + TEST, line):
                self.progress("fixtures")

    def source_tree(self, commit, name):
        root = checkout(self.deps, self.cat, self.request["repo_id"], commit)
        return snapshot(self.deps, self.cat, root, commit, self.folder / name)

    def source(self):
        source = self.source_tree(self.request["commit"], "source")
        tests = self.request.get("tests", [])
        self.validate_tests(source, tests)
        env = build_env(self.deps, self.prof, self.folder, source)
        argv = [self.deps.config.dependency_python, "-I", "-B", "tests/run_all.py", "--jobs", str(self.request["workers"]), *tests]
        code, text = self.stream(argv, source, env, "source")
        rows, totals, complete = parse_source(text)
        failed = [row.name for row in rows if row.status == "failed"]
        base = self.request.get("compare_with_base")
        if base and failed:
            base_tree = self.source_tree(base, "base")
            existing = [name for name in failed if (base_tree / name).is_file()]
            if existing:
                self.progress("base_comparison")
                base_code, base_text = self.stream([self.deps.config.dependency_python, "-I", "-B", "tests/run_all.py",
                    "--jobs", str(self.request["workers"]), *existing], base_tree,
                    build_env(self.deps, self.prof, self.folder, base_tree), "base")
                base_rows, _, base_complete = parse_source(base_text)
                outcomes = {row.name: row.status for row in base_rows} if base_complete else {}
                rows = [row.model_copy(update={"also_fails_on_base": outcomes.get(row.name) == "failed"
                        if outcomes.get(row.name) in ("passed", "failed") else None}) if row.status == "failed" else row for row in rows]
        result = SourceResult(commit=self.request["commit"], base_commit=base, passed=totals[0], failed=totals[1], skipped=totals[2],
            tests=rows[:100], failing_names=failed[:100], truncated=len(rows) > 100 or len(failed) > 100,
            exit=code, coverage="named" if tests else "suite", table_complete=complete,
            artifacts=[a.uri for a in self.artifacts])
        # Complete per-test results remain in a registered artifact rather than an oversized event.
        self.result(result.model_dump(), extra={"all_tests": [row.model_dump() for row in rows]})
        return code if complete else 2

    def validate_tests(self, source, names):
        for name in names:
            safe_relative(name)
            prerequisite(self.deps, source / name)
        prerequisite(self.deps, source / "tests/run_all.py")

    def windows(self):
        source = self.source_tree(self.request["commit"], "source")
        tests = self.request.get("fixtures", [])
        self.validate_tests(source, tests)
        output = self.folder / "windows"
        self.deps.paths.output(output)
        argv = [self.cat.windows_check, source, self.request["base"], "--output", output, "--sysroot", self.cat.windows_sysroot]
        for name in tests:
            argv.extend(["--test", name])
        env = build_env(self.deps, self.prof, self.folder, source)
        env["HS_PYTHON"] = str(self.deps.config.dependency_python)
        code, _ = self.stream(argv, source, env, "windows")
        report_path = prerequisite(self.deps, output / "report.json")
        report = json_read(report_path)
        if report.get("head") != self.request["commit"] or report.get("base") != self.request["base"]:
            raise self.deps.error("backend_failed", "checks_windows_identity")
        self.artifact(report_path, "application/json")
        result = parse_windows(report, code)
        self.result(result.model_dump())
        return code

    def pr(self):
        root = checkout(self.deps, self.cat, self.request["repo_id"], self.request["commit"])
        if self.cat.pr_check_checkout is None or self.deps.paths.read(self.cat.pr_check_checkout) != root:
            raise self.deps.error("unavailable", "checks_pr_repository_binding")
        if git(self.cat, root, "rev-parse", self.request["branch"] + "^{commit}") != self.request["commit"]:
            raise self.deps.error("refused_by_policy", "checks_pr_head")
        title = self.deps.registry.artifact_path(self.request["title_artifact"], self.deps.paths)
        artifact = self.deps.registry.get_artifact(self.request["title_artifact"])
        if file_hash(title) != artifact.sha256:
            raise self.deps.error("refused_by_policy", "checks_pr_title")
        argv = [self.cat.pr_check, self.request["branch"], title]
        for pair in self.request.get("imported", []):
            argv.extend(["--imported", pair["commit"] + "=" + pair["original"]])
        env = build_env(self.deps, self.prof, self.folder, root)
        env.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
        code, text = self.stream(argv, root, env, "preflight")
        if git(self.cat, root, "rev-parse", self.request["branch"] + "^{commit}") != self.request["commit"]:
            raise self.deps.error("uncertain_outcome", "checks_pr_head")
        result = parse_pr(text, code)
        self.result(result.model_dump(by_alias=True))
        return code if result.passed is not None else 2

    def build(self):
        plan = self.request["plan"]
        if plan["plan_hash"] not in self.cat.approved_plans:
            raise self.deps.error("refused_by_policy", "build_start")
        _, identity = profile(self.deps, self.cat, self.request["profile_id"], build=True)
        if identity != plan["profile_identity"]:
            raise self.deps.error("refused_by_policy", "build_start")
        source = self.source_tree(self.request["commit"], "source")
        output = self.folder / "output"
        self.deps.paths.output(output)
        donor = donor_record(self.deps, plan["donor_id"]) if plan["mode"] == "reuse" else None
        if donor:
            copy_tree(self.deps, self.cat, Path(donor["source"]) / "work", source / "work")
            copy_tree(self.deps, self.cat, Path(donor["output"]), output)
        else:
            seed_profile(self.deps, self.prof, source)
        env = build_env(self.deps, self.prof, self.folder, source)
        if donor:
            self.progress("compile")
            argv = [self.deps.config.dependency_python, "-I", "-u", "nx2/build.py", "--standalone", "--jobs", str(self.prof.jobs)]
        else:
            argv = [self.deps.config.dependency_python, "-I", "-u", "nx2/make_game.py", "--romfs", self.prof.romfs,
                "--exefs", self.prof.exefs, "--out", output, "--work", source / "work/build", "--jobs", str(self.prof.jobs),
                "--no-prepare-shaders", "--log", self.folder / "make-game.log"]
        code, text = self.stream(argv, source, env, "build", build=True)
        if code:
            self.result({"completed_receipt": False, "exit": code})
            return code
        stamps = set(re.findall(r"^version: .*\(source ([0-9a-f]+)\)", text, re.M))
        if len(stamps) != 1:
            raise self.deps.error("backend_failed", "build_source_stamp")
        if donor:
            linked = [name for name in ("hookshot", "hookshot_vk.so")
                      if re.search(r"^linked .*/" + re.escape(name) + r":", text, re.M)]
            if len(linked) != 2:
                raise self.deps.error("backend_failed", "build_receipt")
            self.progress("finish")
            for name in linked:
                src = prerequisite(self.deps, source / "work/build/sa/build" / name)
                dst = output / name
                # Only fresh attempt outputs; donor files themselves remain untouched.
                with src.open("rb") as stream, dst.open("wb") as target:
                    while chunk := stream.read(1024 * 1024):
                        target.write(chunk)
        elif "@@done " + str(output) not in text.splitlines():
            raise self.deps.error("backend_failed", "build_receipt")
        hashes = {name: file_hash(prerequisite(self.deps, output / name)) for name in ("hookshot", "hookshot_vk.so")}
        # A build donor is never a played copy; no saves or run folders may enter its manifest.
        if any((output / name).exists() for name in ("runs", "user", "user-template")):
            raise self.deps.error("refused_by_policy", "build_output")
        record = {"kind": "build_manifest", "job_id": self.job_id, "commit": self.request["commit"],
            "source_stamp": next(iter(stamps)), "binary_hashes": hashes, "source": str(source), "output": str(output),
            "work": str(source / "work"), "work_identity": tree_identity(self.deps, source / "work"),
            "profile_identity": plan["profile_identity"], "lift_group": plan["lift_group"], "object_keys": dry_run(text),
            "completed_receipt": {"kind": "linked_outputs" if donor else "builder_done", "exit": code, "observed_at": utc_now()}}
        path = self.folder / "build-manifest.json"
        write_new(self.deps, path, record)
        self.artifact(path, "application/json")
        self.deps.registry.update_run(self.payload["run_ids"][0], build=Build(commit=self.request["commit"], source_stamp=record["source_stamp"],
            binary_hashes=hashes, profile_id=self.request["profile_id"]), artifacts=[a.id for a in self.artifacts])
        self.result({"build_id": self.payload["run_ids"][0], "profile_id": self.request["profile_id"],
                     **{key: record[key] for key in ("commit", "source_stamp", "binary_hashes", "completed_receipt")}})
        return 0

    def result(self, result, extra=None):
        path = self.folder / "result.json"
        write_new(self.deps, path, {**result, **(extra or {})})
        artifact = self.artifact(path, "application/json")
        # Summaries remain small; the complete typed result is retained separately.
        summary = {key: value for key, value in result.items() if key not in ("tests", "failing_names", "checks", "commands", "artifacts")}
        self.deps.registry.append_event(self.job_id, "check_result", {"result": summary, "artifact": artifact.uri})
        for key in ("tests", "checks", "commands"):
            rows = (extra or {}).get("all_tests", result.get(key, [])) if key == "tests" else result.get(key, [])
            for index, row in enumerate(rows):
                # Individual rows fit the shared event cap; job_read provides its cursor.
                encoded = json.dumps(row)
                if len(encoded.encode()) <= 12000:
                    self.deps.registry.append_event(self.job_id, "check_row",
                        {"kind": key, "index": index, "row": row, "artifact": artifact.uri})


def main():
    request_path = Path(sys.argv[1])
    payload = json_read(request_path, 524288)
    deps = dependencies(payload)
    job_id = payload["job_id"]
    try:
        cat = catalogue(deps)
        request = payload["request"]
        prof = cat.profiles.get(request.get("profile_id", "default"))
        if prof is None:
            raise deps.error("unavailable", payload["operation"])
        if len(sys.argv) == 2:
            # Admission happens in the inner worker, after every gate/lock has admitted it.
            command = [sys.executable, "-I", "-m", "src.effects.checks_worker", str(request_path), "--inside"]
            if payload["operation"] == "build_start":
                command = [str(deps.config.build_gate), *command]
                if request["plan"]["mode"] == "full":
                    command = [str(cat.flock), str(cat.full_build_lock), *command]
            elif payload["operation"] in ("checks_source", "checks_windows_syntax"):
                command = [str(cat.idle), str(cat.nocore), *command]
            else:
                command = [str(cat.flock), str(request_path.parent / ("fetch-" + request["repo_id"] + ".lock")), *command]
            deps.registry.append_event(job_id, "queue", {"lane": payload["lane"]})
            deps.registry.close()
            os.execv(command[0], command)
        deps.policy.authorize(lane=payload["lane"], resource_class="ordinary", wall_seconds=prof.wall_seconds,
                              plan_hash=payload["plan_hash"], stage="admission")
        checkout(deps, cat, request["repo_id"], request["commit"])
        deps.supervisor.mark_admitted(job_id)
        for run_id in payload["run_ids"]:
            deps.registry.update_run(run_id, state=State.RUNNING, started_at=utc_now())
        folder = request_path.parent / (job_id + "-output")
        deps.paths.output(folder).mkdir(mode=0o700)
        worker = Worker(deps, payload, folder)
        worker.progress("starting")
        code = {"checks_source": worker.source, "checks_windows_syntax": worker.windows,
                "checks_pr": worker.pr, "build_start": worker.build}[payload["operation"]]()
        return code
    except Exception as exc:
        error = exc.envelope if isinstance(exc, WorkbenchError) else deps.error("backend_failed", payload["operation"]).envelope
        deps.registry.update_job(job_id, error=error)
        deps.registry.append_event(job_id, "check_error", error.model_dump())
        return 2
    finally:
        deps.registry.close()


if __name__ == "__main__":
    raise SystemExit(main())
