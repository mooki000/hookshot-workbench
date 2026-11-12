"""Bounded GET-only CI adapter, reusing the installed evidence reader's parser."""
import importlib.util
import json
import os
from pathlib import Path
import re
import time
from src.models import utc_now
from src.models.checks import CICheck, CIResult, FailedTest
from src.effects.checks_common import catalogue, capture, prerequisite, safe_text

BAD = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"}


def reader_module(deps, cat):
    path = prerequisite(deps, cat.ci_reader)
    spec = importlib.util.spec_from_file_location("workbench_checked_ci_reader", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "failures", None)):
        raise deps.error("unavailable", "ci_read_parser")
    return module


class CIReader:
    def __init__(self, deps, cat, repository):
        self.deps, self.cat, self.repository = deps, cat, repository
        self.module = reader_module(deps, cat)
        self.deadline = time.monotonic() + 25
        self.cache = {}

    def gh(self, *args, raw=False):
        key = (args, raw)
        if key not in self.cache:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise self.deps.error("timeout", "ci_read")
            # The trusted CLI owns its existing session; no token discovery/read endpoint.
            env = {"PATH": os.defpath, "GH_PROMPT_DISABLED": "1", "GH_PAGER": "cat"}
            if os.environ.get("HOME"):
                env["HOME"] = os.environ["HOME"]
            code, text = capture([self.cat.gh, *args], cwd=self.cat.gh.parent, env=env, timeout=remaining)
            if code:
                raise self.deps.error("unavailable", "ci_read")
            self.cache[key] = text if raw else json.loads(text)
        return self.cache[key]

    def api(self, suffix):
        return self.gh("api", "repos/" + self.repository + "/" + suffix)

    def log(self, job_id):
        text = self.gh("api", "repos/" + self.repository + "/actions/jobs/" + str(int(job_id)) + "/logs",
                       "--allow-escape-sequences", raw=True)
        return self.module.failures(text)

    def main(self):
        runs = self.api("actions/runs?branch=main&status=completed&per_page=40").get("workflow_runs", [])
        latest = {}
        for run in runs:
            if run.get("status") == "completed":
                latest.setdefault(run["workflow_id"], run)
        return latest

    def matching_main(self, latest, workflow, job_name, names):
        run = latest.get(workflow)
        if run is None:
            return {}, "no_completed_main_run"
        # One bounded page is explicit coverage, never a claim of exhaustive CI history.
        data = self.api("actions/runs/" + str(int(run["id"])) + "/jobs?per_page=100&page=1")
        matches = [j for j in data.get("jobs", []) if j.get("name") == job_name and str(j.get("conclusion") or "").upper() in BAD]
        observed = set()
        for job in matches[:5]:
            parsed = self.log(job["id"])
            observed.update(parsed.get("tests", []) + parsed.get("cases", []))
        return {name: run.get("head_sha") for name in names if name in observed}, None

    def check(self, check, latest):
        conclusion = check.get("conclusion") or check.get("state")
        url = check.get("detailsUrl") or check.get("targetUrl") or check.get("html_url")
        prefix = "https://github.com/" + self.repository + "/"
        url = url if isinstance(url, str) and url.startswith(prefix) and len(url) <= 500 else None
        name = safe_text(str(check.get("name") or check.get("context") or "unknown"), 200)
        names, unknown, matched = [], None, {}
        if str(conclusion or "").upper() in BAD:
            try:
                job_id = check.get("id") if "run_id" in check else None
                if job_id is None:
                    match = re.search(r"/jobs?/(\d+)", url or "")
                    job_id = int(match[1]) if match else None
                if job_id is None:
                    unknown = "no_actions_job_log"
                else:
                    parsed = self.log(job_id)
                    names = sorted(set(parsed.get("tests", []) + parsed.get("cases", [])))
                    names = [safe_text(n, 300) for n in names if isinstance(n, str)]
                    if not names:
                        unknown = "no_recognized_failing_test_names"
                    run_id = check.get("run_id")
                    if run_id is None:
                        job = self.api("actions/jobs/" + str(job_id))
                        run_id = job.get("run_id")
                    if run_id is not None and names:
                        run = self.api("actions/runs/" + str(int(run_id)))
                        matched, main_unknown = self.matching_main(latest, run.get("workflow_id"), name, names)
                        unknown = unknown or main_unknown
            except Exception:
                unknown = "log_or_main_comparison_unavailable"
        return CICheck(name=name, conclusion=safe_text(str(conclusion), 80) if conclusion is not None else None,
            url=url, failed_tests=[FailedTest(name=n, also_red_on_main="yes" if n in matched else "unknown", main_head=matched.get(n)) for n in names[:100]],
            unknown=unknown, truncated=len(names) > 100)


def ci_read_impl(deps, repository, pull_request=None, run_id=None, cursor=0, limit=10):
    if (pull_request is None) == (run_id is None) or not 1 <= limit <= 100 or cursor < 0:
        raise deps.error("invalid_argument", "ci_read")
    cat = catalogue(deps)
    if repository not in cat.repositories:
        raise deps.error("refused_by_policy", "ci_read_repository")
    prerequisite(deps, cat.gh)
    reader = CIReader(deps, cat, repository)
    unknown = []
    try:
        main = reader.main()
        if not main:
            unknown.append("no_completed_main_run")
    except Exception:
        main = {}
        unknown.append("main_history_unavailable")
    if pull_request is not None:
        pr = reader.gh("pr", "view", str(pull_request), "--repo", repository, "--json",
                       "number,headRefOid,reviewDecision,statusCheckRollup")
        all_checks = pr.get("statusCheckRollup", [])
        selected = all_checks[cursor:cursor + limit]
        next_cursor = cursor + limit if len(all_checks) > cursor + limit else None
        head, review = pr.get("headRefOid"), pr.get("reviewDecision") or None
    else:
        run = reader.api("actions/runs/" + str(run_id))
        # Cursor indexes jobs; bounded API pages supply enough data without --paginate.
        start_page, offset = divmod(cursor, 100)
        page = reader.api("actions/runs/" + str(run_id) + "/jobs?per_page=100&page=" + str(start_page + 1))
        jobs = page.get("jobs", [])
        selected = jobs[offset:offset + min(limit, 100 - offset)]
        total = page.get("total_count")
        next_cursor = cursor + len(selected) if selected and (total is None and len(jobs) == 100 or total is not None and cursor + len(selected) < total) else None
        selected = [{**job, "run_id": run_id} for job in selected]
        head, review = run.get("head_sha"), None
        if run.get("status") != "completed":
            unknown.append("run_not_completed")
    checks = [reader.check(check, main) for check in selected]
    return CIResult(repository=repository, pull_request=pull_request, run_id=run_id, head=head, review_state=review,
                    checks=checks, next_cursor=next_cursor, unknown=unknown, observed_at=utc_now())
