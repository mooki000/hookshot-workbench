#!/usr/bin/env python3
"""Offline queue tests; no network, game, compiler, account writes or external posts."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("pr_loop", Path(__file__).with_name("pr_loop.py"))
loop = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(loop)
NOW = "2026-11-12T12:00:00Z"
EARLIER = "2026-11-12T10:00:00Z"
HEAD = "a" * 40
NEW = "b" * 40
ME = "@tester:example.invalid"


def pr(number=1, author="nullifyshot", **changes):
    value = {"number": number, "author": author, "title": "fix: a testable change", "body": "A rendering change.",
             "draft": False, "association": "CONTRIBUTOR", "head": HEAD,
             "created_at": EARLIER, "updated_at": NOW, "url": f"https://github.com/{loop.REPO}/pull/{number}",
             "state": "OPEN", "ended_at": None, "labels": [], "changed_files": 1,
             "events": [], "files": ["gpu/vk/render.cpp"]}
    value.update(changes)
    return value


def event(number=1, author="mooki000", at=EARLIER, body="Built " + HEAD + "; tests pass.", kind="comment", **changes):
    fragment = "discussion_r" if kind == "inline" else "pullrequestreview-" if kind == "review" else "issuecomment-"
    value = {"id": kind + ":1", "kind": kind, "author": author, "association": "CONTRIBUTOR",
             "at": at, "created_at": at, "body": body, "url": f"https://github.com/{loop.REPO}/pull/{number}#{fragment}1",
             "state": None, "commit": HEAD}
    value.update(changes)
    return value


def request(number=1, tid="T-ABCD", **changes):
    value = {"id": tid, "pr": number, "requester": "@author:example.invalid", "requested": loop.epoch(EARLIER),
             "summary": "Test on Linux", "needs": "Linux NVIDIA RTX 4090", "notes": "Compare with main.",
             "status": "open", "approved": True, "pr_state": "open", "claims": {}, "results": {}}
    value.update(changes)
    return value


def snapshot(*prs, requests=()):
    state = loop.new_state()
    state["snapshot"] = {"at": NOW, "prs": {str(p["number"]): p for p in prs}, "requests": list(requests),
                         "request_user": ME, "requests_available": True, "warnings": []}
    return state


class FakeBackend:
    def __init__(self, prs, requests=(), available=True):
        self.prs = {str(p["number"]): copy.deepcopy(p) for p in prs}
        self.queue = list(requests)
        self.available = available

    def open_prs(self):
        return {k: copy.deepcopy(p) for k, p in self.prs.items() if p["state"] == "OPEN"}

    def detail(self, number):
        return copy.deepcopy(self.prs[str(number)])

    def enrich(self, value):
        return value

    def requests(self):
        return self.queue, ME, [] if self.available else ["not signed in; continuing without requests"], self.available


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.config = loop.defaults(["NotRexed", "hckrman101", "AnotherMaintainer"], "fixture", NOW)

    def take(self, state, number=1, reviewed=True):
        loop.take(state, self.config, number, EARLIER)
        if reviewed:
            loop.reviewed(state, number, state["snapshot"]["prs"][str(number)]["head"], "Read all executable changes; no unexpected effects.", EARLIER)
        return state["active"][str(number)]

    def scan(self, state, prs, requests=(), at=NOW):
        return loop.scan(state, self.config, FakeBackend(prs, requests), at)

    def test_default_priorities_and_hardware(self):
        self.assertEqual(self.config["priority_authors"][:len(loop.PRIORITY_GROUP) + 1], [*loop.PRIORITY_GROUP, "NotRexed"])
        self.assertIn('"null"', self.config["comment"])
        self.assertEqual(self.config["max_active"], 4)
        self.assertEqual(loop.priority("SadTransGirl", self.config), 1)
        self.assertEqual(loop.priority("NotRexed", self.config), 2)
        loop.validate_config(self.config)

    def test_a_reply_beats_retest_claim_and_new_work(self):
        state = snapshot(pr(1), pr(2), pr(3), pr(4))
        a, b, c = (self.take(state, n) for n in (1, 2, 3))
        a["wait_for"] = "our-reply"
        b.update(last_tested_head=NEW, head_moved=True)
        c.update(wait_for="maintainer", claim={"request_id": "T-ABCD", "expires_at": "2026-11-12T12:05:00Z", "status": "active"})
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "reply")
        self.assertEqual(loop.next_action(state, self.config, NOW)["number"], 1)

    def test_b_retest_beats_claim_due_and_new_work(self):
        state = snapshot(pr(1), pr(2), requests=[request(claims={ME: loop.epoch(NOW) + 300})])
        record = self.take(state)
        record.update(last_tested_head=NEW, head_moved=True)
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "retest")

    def test_c_claim_due_beats_new_work(self):
        state = snapshot(pr(1), pr(2), requests=[request(claims={ME: loop.epoch(NOW) + 300})])
        record = self.take(state)
        record["wait_for"] = "maintainer"
        chosen = loop.next_action(state, self.config, NOW)
        self.assertEqual((chosen["action"], chosen["number"]), ("renew-or-finish", 1))

    def test_d_priority_then_request_gap_age(self):
        state = snapshot(pr(1, author="NotRexed"), pr(2), pr(3, body="## Not tested\nLinux NVIDIA\n## Risks\nNone."),
                         pr(4, created_at="2026-11-10T10:00:00Z"), requests=[request(2)])
        self.assertEqual(loop.next_action(state, self.config, NOW)["number"], 2)
        self.assertEqual([c["number"] for c in loop.candidates(state, self.config)], [2, 3, 4, 1])

    def test_priority_authors_rotate_case_insensitively(self):
        state = snapshot(pr(1), pr(2), pr(3, "hckrman101"), pr(4, "SadTransGirl"), pr(5, "klimovich008"))
        self.assertEqual([c["number"] for c in loop.candidates(state, self.config)][:4], [1, 3, 4, 5])
        record = self.take(state)
        record["wait_for"] = "maintainer"
        self.assertEqual(loop.next_action(state, self.config, NOW)["number"], 3)

    def test_hardware_fit_precedes_author_tie(self):
        state = snapshot(pr(1), pr(2, "klimovich008"), requests=[request(2)])
        self.assertEqual(loop.next_action(state, self.config, NOW)["number"], 2)

    def test_ready_priority_draft_is_not_blanket_excluded(self):
        state = snapshot(pr(draft=True, body="Draft: please test on Linux."))
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "take")
        state["snapshot"]["prs"]["1"]["body"] = "Still a draft."
        self.assertIn("no testing invitation", " ".join(loop.candidates(state, self.config)[0]["exclusions"]))
        state["snapshot"]["requests"] = [request()]
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "take")

    def test_parked_first_lines_override_request(self):
        for text in ("Draft. Testers: please hold off", "Parked. Not for testing or merging.", "Do not test this."):
            with self.subTest(text=text):
                state = snapshot(pr(draft=True, body=text), requests=[request()])
                self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "wait")
                self.assertIn("author says to hold off", " ".join(loop.exclusions(state, self.config)))

    def test_current_head_already_tested(self):
        state = snapshot(pr(events=[event()]))
        self.assertIn("already tested", " ".join(loop.exclusions(state, self.config)))
        with self.assertRaises(loop.LoopError):
            loop.take(state, self.config, 1, NOW)
        state["snapshot"]["prs"]["1"]["head"] = NEW
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "take")

    def test_not_tested_promise_is_not_a_result(self):
        for text in ("Not tested " + HEAD, "Will test " + HEAD, "Not built " + HEAD):
            self.assertIsNone(loop.own_test(pr(events=[event(body=text)]), "mooki000"))

    def test_unapproved_priority_card_is_not_a_claim_or_pr_ban(self):
        for approved in (False, None):
            state = snapshot(pr(author="klimovich008"), requests=[request(approved=approved)])
            self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "take")
            with self.assertRaises(loop.LoopError):
                loop.take(state, self.config, 1, NOW, request_id="T-ABCD")
            record = self.take(state)
            self.assertEqual(record["admission"], "priority author on the user's word; card T-ABCD exists but is not approved: no claim")
            self.assertIsNone(record["request_id"])
            self.assertIsNone(record["claim"])
            self.assertEqual(record["deferred_request_ids"], ["T-ABCD"])
            self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "test")

    def test_unapproved_card_alone_is_not_a_lowest_tier_invitation(self):
        state = snapshot(pr(author="stranger"), requests=[request(approved=False)])
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "wait")
        self.assertIn("unapproved card", " ".join(loop.exclusions(state, self.config)))
        with self.assertRaises(loop.LoopError):
            loop.take(state, self.config, 1, NOW)
        state["snapshot"]["prs"]["1"]["body"] = "Please test on Linux."
        record = self.take(state)
        self.assertIsNone(record["request_id"])
        self.assertTrue(record["admission"].startswith("asked in the thread/description"))
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "test")

    def test_priority_draft_card_invites_but_never_overrides_parked(self):
        state = snapshot(pr(draft=True), requests=[request(approved=False)])
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "take")
        state["snapshot"]["prs"]["1"]["body"] = "Parked. Not for testing."
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "wait")

    def test_later_card_approval_attaches_existing_result_in_order(self):
        state = snapshot(pr(), requests=[request(approved=False)])
        record = self.take(state)
        state["snapshot"]["prs"]["1"]["events"] = [event()]
        loop.tested(state, self.config, 1, HEAD, "pass", event()["url"], NOW)
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "wait")
        self.scan(state, [pr(events=[event()])], [request()])
        chosen = loop.next_action(state, self.config, NOW)
        self.assertEqual(chosen["action"], "claim-result")
        self.assertEqual((chosen["request_id"], chosen["verdict"], chosen["comment"]), ("T-ABCD", "pass", event()["url"]))
        self.assertIn("claim-result", loop.report(state, self.config, NOW, EARLIER))
        self.scan(state, [pr(events=[event()])], [request(claims={ME: loop.epoch(NOW) + 5400})])
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "done-result")
        self.assertEqual(record["claim"]["status"], "active")
        self.scan(state, [pr(events=[event()])], [request(results={ME: {"result": "pass", "comment": event()["url"], "time": loop.epoch(NOW)}})])
        self.assertEqual(record["claim"]["status"], "finished")
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "wait")
        self.assertEqual(record["last_tested_head"], HEAD)

    def test_existing_result_bookkeeping_requires_current_head_fit_and_access(self):
        state = snapshot(pr(), requests=[request(approved=False)])
        self.take(state)
        state["snapshot"]["prs"]["1"]["events"] = [event()]
        loop.tested(state, self.config, 1, HEAD, "mixed", event()["url"], NOW)
        for change in ("head", "hardware", "access", "approval"):
            with self.subTest(change=change):
                view = copy.deepcopy(state)
                view["snapshot"]["requests"][0]["approved"] = True
                if change == "head":
                    view["snapshot"]["prs"]["1"]["head"] = NEW
                elif change == "hardware":
                    view["snapshot"]["requests"][0]["needs"] = "Windows"
                elif change == "access":
                    view["snapshot"]["requests_available"] = False
                else:
                    view["snapshot"]["requests"][0]["approved"] = False
                self.assertEqual(loop.result_actions(view, self.config, NOW), [])

    def test_existing_result_bookkeeping_does_not_take_a_second_claim(self):
        state = snapshot(pr(), pr(2), requests=[request(approved=False)])
        self.take(state)
        state["snapshot"]["prs"]["1"]["events"] = [event()]
        loop.tested(state, self.config, 1, HEAD, "fail", event()["url"], NOW)
        self.scan(state, [pr(events=[event()]), pr(2)], [request(), request(2, "T-EFGH", claims={ME: loop.epoch(NOW) + 5400})])
        self.assertEqual(loop.result_actions(state, self.config, NOW), [])
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "finish-claim")

    def test_approval_before_first_test_offers_claim_not_result(self):
        state = snapshot(pr(), requests=[request(approved=False)])
        self.take(state)
        self.scan(state, [pr()], [request()])
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "claim")
        self.assertFalse(loop.result_actions(state, self.config, NOW))

    def test_unexplained_tools_flag_main_and_refuse_take(self):
        state = snapshot(pr(files=["scripts/build.py"], body="Improves rendering."))
        chosen = loop.next_action(state, self.config, NOW)
        self.assertEqual(chosen["action"], "diff-review")
        self.assertEqual(chosen["files"], ["scripts/build.py"])
        with self.assertRaises(loop.LoopError):
            loop.take(state, self.config, 1, NOW)
        loop.take(state, self.config, 1, NOW, reviewed_head=HEAD, review_note="Read build.py: explained compiler switch only.")
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "test")

    def test_every_first_build_requires_recorded_review(self):
        state = snapshot(pr())
        self.take(state, reviewed=False)
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "diff-review")
        loop.reviewed(state, 1, HEAD, "Read runtime diff.", NOW)
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "test")

    def test_new_head_invalidates_review(self):
        state = snapshot(pr())
        record = self.take(state)
        record["last_tested_head"] = HEAD
        self.scan(state, [pr(head=NEW)])
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "diff-review")
        loop.reviewed(state, 1, NEW, "Read new runtime diff.", NOW)
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "retest")

    def test_lowest_tier_needs_request_or_gap(self):
        state = snapshot(pr(author="stranger"))
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "wait")
        for body in ("Please test on Linux.", "## Not tested\nLinux on NVIDIA\n## Risks\nNone"):
            state["snapshot"]["prs"]["1"]["body"] = body
            self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "take")
        state["snapshot"]["prs"]["1"]["body"] = "A change."
        state["snapshot"]["prs"]["1"]["events"] = [event(author="stranger", body="Could someone test this on Linux?")]
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "take")

    def test_skip_user_and_hardware_mismatch(self):
        state = snapshot(pr(author="mooki000"), pr(2), requests=[request(2, needs="AMD RX 7000 or newer, or Intel Arc")])
        text = "\n".join(loop.exclusions(state, self.config))
        self.assertIn("skip_authors", text)
        self.assertIn("no open request fits", text)
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "wait")

    def test_real_queue_not_tried_is_an_untested_gap(self):
        body = "**Not tried:** a real game build, or a real downloaded release, through the new screens on Linux."
        self.assertIn("Linux", loop.not_tested_gap(body, self.config["hardware"]))
        state = snapshot(pr(1), pr(2, "madebyjordan", body=body))
        self.assertEqual(loop.next_action(state, self.config, NOW)["number"], 2)

    def test_real_queue_stem_mentions_still_require_main_review(self):
        state = snapshot(pr(files=["tests/test_output_resolution.py"], body="Updated `test_output_resolution` for the new layout."))
        self.assertFalse(loop.unexplained_files(state["snapshot"]["prs"]["1"]))
        self.take(state, reviewed=False)
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "diff-review")

    def test_real_queue_controller_need_is_not_assumed_available(self):
        self.assertEqual(loop.hardware_fit("any game controller on Windows or Linux", self.config["hardware"])[0], "unknown")

    def test_hardware_families_capacity_os_and_unknown(self):
        cases = {"": "fit", "Linux": "fit", "NVIDIA RTX 4090, Linux": "fit", "NVIDIA RTX 20-50": "fit",
                 "Intel UHD 770 Mesa": "fit", "Intel Arc": "no", "AMD RX 6000 or newer": "no",
                 "8 GB NVIDIA or AMD card, Windows": "no", "8 GB NVIDIA on Linux": "no",
                 "24 GB NVIDIA on Linux": "fit", "without mesh shaders": "fit", "Windows": "no",
                 "Steam Deck": "no", "HDR Linux": "unknown", "windowed Linux": "no",
                 "the odd device I mentioned": "unknown", "at least 16 GB NVIDIA": "fit"}
        for needs, expected in cases.items():
            with self.subTest(needs=needs):
                self.assertEqual(loop.hardware_fit(needs, self.config["hardware"])[0], expected)

    def test_merged_and_closed_move_to_done_with_date(self):
        for ending in ("MERGED", "CLOSED"):
            state = snapshot(pr())
            self.take(state)
            self.scan(state, [pr(state=ending, ended_at=NOW)])
            self.assertFalse(state["active"])
            self.assertEqual(state["done"]["1"]["finished_at"], NOW)
            self.assertEqual(state["done"]["1"]["end_reason"], ending.lower())
            self.assertEqual(state["done"]["1"]["history"][-1]["action"], "done")

    def test_head_moved_does_not_overwrite_tested_head(self):
        state = snapshot(pr())
        record = self.take(state)
        record.update(last_tested_head=HEAD, last_tested_at=EARLIER, verdict="pass", comment_url=event()["url"], wait_for="maintainer")
        self.scan(state, [pr(head=NEW)])
        self.assertEqual(record["last_tested_head"], HEAD)
        self.assertEqual(record["head"], NEW)
        self.assertEqual(record["wait_for"], "our-test")
        loop.validate_state(state)

    def test_review_and_inline_reply_since_our_comment(self):
        for kind in ("comment", "review", "inline"):
            state = snapshot(pr())
            record = self.take(state)
            own = event(at="2026-11-12T10:10:00Z")
            answer = event(author="hckrman101", at="2026-11-12T11:00:00Z", body="Could you test this again?", kind=kind,
                           association="COLLABORATOR")
            self.scan(state, [pr(events=[own, answer])])
            self.assertEqual(record["wait_for"], "our-reply")
            self.assertEqual(record["new_events"][0]["kind"], kind)
            loop.waiting(state, self.config, 1, "maintainer", NOW, reason="Read; no answer needed.")
            self.scan(state, [pr(events=[own, answer])], at="2026-11-12T12:01:00Z")
            self.assertEqual(record["wait_for"], "maintainer")

    def test_lapsed_claim_and_reclaim(self):
        until = loop.epoch(EARLIER) + 90 * 60
        req = request(claims={ME: until})
        state = snapshot(pr(), requests=[req])
        record = self.take(state)
        record["wait_for"] = "maintainer"
        self.scan(state, [pr()], [request()])
        self.assertEqual(record["claim"]["status"], "expired")
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "claim-expired")
        self.scan(state, [pr()], [request(claims={ME: loop.epoch(NOW) + 5400})])
        self.assertEqual(record["claim"]["status"], "active")

    def test_claim_observed_after_take_and_only_one_claim(self):
        state = snapshot(pr(), pr(2), requests=[request(), request(2, "T-EFGH")])
        record = self.take(state)
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "claim")
        self.scan(state, [pr(), pr(2)], [request(claims={ME: loop.epoch(NOW) + 5400}), request(2, "T-EFGH")])
        self.assertEqual(record["claim"]["status"], "active")
        with self.assertRaises(loop.LoopError):
            loop.take(state, self.config, 2, NOW)

    def test_remote_result_finishes_claim_not_pr(self):
        state = snapshot(pr(), requests=[request(claims={ME: loop.epoch(NOW) + 5400})])
        record = self.take(state)
        self.scan(state, [pr()], [request(results={ME: {"result": "pass", "comment": event()["url"], "time": loop.epoch(NOW)}})])
        self.assertEqual(record["claim"]["status"], "finished")
        self.assertIn("1", state["active"])

    def test_max_active_no_fifth_take(self):
        state = snapshot(*(pr(n) for n in range(1, 6)))
        for n in range(1, 5):
            self.take(state, n)["wait_for"] = "maintainer"
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "wait")
        with self.assertRaises(loop.LoopError):
            loop.take(state, self.config, 5, NOW)

    def test_tested_requires_full_head_real_own_comment_and_verdict(self):
        state = snapshot(pr())
        self.take(state)
        with self.assertRaises(loop.LoopError):
            loop.tested(state, self.config, 1, HEAD, "pass", event()["url"], NOW)
        state["snapshot"]["prs"]["1"]["events"] = [event()]
        for head, verdict, url in (("aaa", "pass", event()["url"]), (HEAD, "unknown", event()["url"]),
                                   (HEAD, "pass", event(2)["url"])):
            with self.assertRaises(loop.LoopError):
                loop.tested(state, self.config, 1, head, verdict, url, NOW)
        loop.tested(state, self.config, 1, HEAD, "pass", event()["url"], NOW)
        self.assertEqual(state["active"]["1"]["wait_for"], "maintainer")
        loop.validate_state(state)

    def test_drop_is_visible_and_does_not_silently_retake(self):
        state = snapshot(pr())
        self.take(state)
        loop.drop(state, 1, "author asked to stop", NOW)
        self.assertIn("1", state["parked"])
        self.assertIn("locally parked", " ".join(loop.exclusions(state, self.config)))
        loop.take(state, self.config, 1, NOW, reopen=True)
        self.assertIn("1", state["active"])

    def test_done_refuses_open_pr(self):
        state = snapshot(pr())
        self.take(state)
        with self.assertRaises(loop.LoopError):
            loop.finish(state, 1, NOW)

    def test_three_spaced_cycles_need_user_note_not_auto_park(self):
        state = snapshot(pr())
        record = self.take(state)
        record.update(wait_for="author-reply", wait_since="2026-11-01T12:00:00Z")
        self.scan(state, [pr()])
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "park-note")
        self.assertIn("1", state["active"])
        self.assertFalse(state["parked"])

    def test_report_has_one_table_and_finished_filter(self):
        state = snapshot(pr(1), pr(2))
        self.take(state, 1)
        text = loop.report(state, self.config, NOW, "2026-11-01T00:00:00Z")
        self.assertEqual(text.count("| Group |"), 1)
        self.assertIn("In hand", text)
        self.assertIn("Owed by us", text)
        self.assertIn("Candidate", text)

    def test_failed_request_read_retains_cards_without_granting_claims(self):
        state = snapshot(pr(), requests=[request(approved=False)])
        loop.scan(state, self.config, FakeBackend([pr()], available=False), NOW)
        self.assertEqual(state["snapshot"]["requests"][0]["id"], "T-ABCD")
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "take")
        record = self.take(state)
        self.assertIsNone(record["request_id"])
        self.assertEqual(loop.next_action(state, self.config, NOW)["action"], "test")
        other = snapshot(pr(author="stranger"), requests=[request(approved=False)])
        loop.scan(other, self.config, FakeBackend([pr(author="stranger")], available=False), NOW)
        self.assertIn("feed unavailable", " ".join(loop.exclusions(other, self.config)))
        self.assertEqual(loop.next_action(other, self.config, NOW)["action"], "wait")

    def test_our_new_reply_clears_reply_debt(self):
        state = snapshot(pr())
        record = self.take(state)
        record.update(wait_for="our-reply", last_comment_at=EARLIER, wait_since="2026-11-12T11:00:00Z")
        self.scan(state, [pr(events=[event(at="2026-11-12T11:30:00Z", body="Thanks, here is the measured result.")])])
        self.assertEqual(record["wait_for"], "author-reply")

    def test_reply_debt_beats_stale_note_even_if_newer(self):
        state = snapshot(pr(1), pr(2))
        first, second = self.take(state, 1), self.take(state, 2)
        first.update(wait_for="author-reply", unanswered_cycles=3)
        second.update(wait_for="our-reply", wait_since=NOW)
        self.assertEqual(loop.next_action(state, self.config, NOW)["number"], 2)

    def test_priority_changes_validate_and_record_user_word(self):
        class Validator:
            def __init__(self):
                self.calls = []
            def validate_author(self, handle):
                self.calls.append(handle)
                if handle == "missing":
                    raise loop.LoopError("no GitHub user")
                return handle
        backend = Validator()
        loop.change_priority(self.config, backend, "add", "NewAuthor", "hckrman101", NOW)
        self.assertEqual(self.config["priority_authors"][2], "NewAuthor")
        self.assertEqual(self.config["round_robin_authors"][2], "NewAuthor")
        self.assertEqual(backend.calls, ["NewAuthor", "hckrman101"])
        self.assertEqual(self.config["notes"][-1]["basis"], "on the user's word")
        self.assertEqual(self.config["notes"][-1]["at"], NOW)
        with self.assertRaises(loop.LoopError):
            loop.change_priority(self.config, backend, "add", "NewAuthor", None, NOW)
        before = copy.deepcopy(self.config)
        with self.assertRaises(loop.LoopError):
            loop.change_priority(self.config, backend, "add", "missing", None, NOW)
        self.assertEqual(self.config, before)
        loop.change_priority(self.config, backend, "remove", "NewAuthor", None, NOW)
        self.assertNotIn("NewAuthor", self.config["priority_authors"])
        self.assertNotIn("NewAuthor", self.config["round_robin_authors"])

    def test_priority_cli_forms_parse(self):
        for args in (["priority", "list"], ["priority", "add", "new", "--after", "nullifyshot"],
                     ["priority", "remove", "new"]):
            self.assertEqual(loop.parser().parse_args(args).command, "priority")


class AnnotationTests(unittest.TestCase):
    def test_missing_annotation_uses_index_state_without_approval(self):
        config = loop.defaults([], "fixture", NOW)
        missing = request(approved=None, pr_state=None)
        no_fields = request(2, "T-EFGH")
        no_fields.pop("pr_state")
        no_fields.pop("approved")
        absent = request(3, "T-JKLM", approved=None, pr_state=None)
        state = loop.new_state()
        backend = FakeBackend([pr(author="hckrman101", association="COLLABORATOR", draft=True), pr(2)],
                              [missing, no_fields, absent])
        loop.scan(state, config, backend, NOW)
        cards = state["snapshot"]["requests"]
        self.assertEqual(len(cards), 3)
        self.assertEqual([c["pr_state"] for c in cards], ["draft", "open", loop.UNKNOWN_REQUEST_STATE])
        self.assertEqual([c["request_script_pr_state"] for c in cards], [loop.UNKNOWN_REQUEST_STATE] * 3)
        self.assertEqual([c["approved"] for c in cards], [None] * 3)
        self.assertEqual(cards[0]["pr_state_source"], "gh pr list")
        self.assertEqual(cards[2]["pr_state_source"], "request script: unknown")
        self.assertTrue(any("approval remains unknown" in w for w in state["snapshot"]["warnings"]))
        with self.assertRaises(loop.LoopError):
            loop.take(state, config, 1, NOW, request_id="T-ABCD")


class BackendTests(unittest.TestCase):
    def test_gh_standin_paginates_all_threads_and_files(self):
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            endpoint = args[-1]
            if "/files?" in endpoint:
                value = [[{"filename": "gpu/vk/render.cpp"}]]
            else:
                value = [[{"id": 1, "user": {"login": "nullifyshot"}, "author_association": "CONTRIBUTOR",
                           "created_at": EARLIER, "submitted_at": EARLIER, "body": "Please test", "html_url": "https://example.invalid"}]]
            return subprocess.CompletedProcess(args, 0, json.dumps(value), "")
        backend = loop.Backend(runner=runner)
        result = backend.enrich(pr())
        self.assertEqual({e["kind"] for e in result["events"]}, {"comment", "review", "inline"})
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(args[:4] == ["gh", "api", "--paginate", "--slurp"] for args in calls))
        self.assertEqual(result["files"], ["gpu/vk/render.cpp"])

    def test_collaborator_403_uses_recent_positive_associations(self):
        def runner(args, **kwargs):
            if "collaborators" in args[-1]:
                return subprocess.CompletedProcess(args, 1, "", "HTTP 403 push access required")
            data = [{"user": {"login": "Owner"}, "author_association": "OWNER"},
                    {"user": {"login": "Member"}, "author_association": "MEMBER"},
                    {"user": {"login": "Contributor"}, "author_association": "CONTRIBUTOR"}]
            return subprocess.CompletedProcess(args, 0, json.dumps(data), "")
        authors, source = loop.Backend(runner=runner).authors()
        self.assertEqual(authors, ["Member", "Owner"])
        self.assertIn("403", source)

    def test_request_not_signed_in_is_nonfatal(self):
        def runner(args, **kwargs):
            self.assertEqual(args[0], sys.executable)
            return subprocess.CompletedProcess(args, 1, "", "test_requests: not signed in")
        with patch.dict(loop.os.environ, {}, clear=True):
            requests, identity, warnings, available = loop.Backend(runner=runner).requests()
        self.assertEqual(requests, [])
        self.assertIsNone(identity)
        self.assertFalse(available)
        self.assertIn("not signed in", warnings[0])

    def test_request_child_is_anonymous_by_default_without_credential_lookup(self):
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            self.assertEqual(args[0], sys.executable)
            self.assertNotIn("GH_TOKEN", kwargs["env"])
            self.assertNotIn("GITHUB_TOKEN", kwargs["env"])
            return subprocess.CompletedProcess(args, 0, json.dumps({"requests": [request()], "user_id": ME}), "")
        with patch.dict(loop.os.environ, {"PATH": "/fixture/bin"}, clear=True):
            cards, _, warnings, available = loop.Backend(runner=runner).requests()
        self.assertTrue(available)
        self.assertTrue(cards[0]["approved"])
        self.assertEqual(warnings, [])
        self.assertEqual(len(calls), 1)

    def test_request_warning_is_retained_without_discarding_json(self):
        def runner(args, **kwargs):
            self.assertEqual(args[0], sys.executable)
            return subprocess.CompletedProcess(args, 0, json.dumps({"requests": [request()], "user_id": ME}), "warning: annotation unavailable")
        with patch.dict(loop.os.environ, {}, clear=True):
            requests, identity, warnings, available = loop.Backend(runner=runner).requests()
        self.assertTrue(available)
        self.assertEqual(identity, ME)
        self.assertEqual(len(requests), 1)
        self.assertIn("annotation unavailable", warnings[0])
        self.assertIsNone(requests[0]["approved"])
        self.assertEqual(requests[0]["pr_state"], loop.UNKNOWN_REQUEST_STATE)

    def test_rate_limited_annotation_keeps_card_without_approving_it(self):
        def runner(args, **kwargs):
            self.assertEqual(args[0], sys.executable)
            return subprocess.CompletedProcess(args, 0, json.dumps({"requests": [request(), request(2, "T-EFGH")], "user_id": ME}),
                                               "warning: GitHub answered 403 for PR #1 (rate limit: set GITHUB_TOKEN)")
        with patch.dict(loop.os.environ, {}, clear=True):
            cards, identity, warnings, available = loop.Backend(runner=runner).requests()
        self.assertTrue(available)
        self.assertEqual(len(cards), 2)
        self.assertIsNone(cards[0]["approved"])
        self.assertEqual(cards[0]["request_script_pr_state"], loop.UNKNOWN_REQUEST_STATE)
        self.assertIs(cards[1]["approved"], True)
        self.assertEqual(cards[1]["pr_state"], "open")
        self.assertIn("403", warnings[0])
        backend = FakeBackend([pr(association="COLLABORATOR"), pr(2)], cards)
        state = loop.new_state()
        loop.scan(state, loop.defaults([], "fixture", NOW), backend, NOW)
        self.assertEqual(state["snapshot"]["requests"][0]["pr_state"], "open")
        self.assertIsNone(state["snapshot"]["requests"][0]["approved"])

    def test_priority_author_requires_existing_user_and_repo_pr(self):
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            if args[:2] == ["gh", "api"]:
                value = {"login": "NewAuthor", "type": "User"}
            else:
                value = [{"number": 42, "author": {"login": "NewAuthor"}}]
            return subprocess.CompletedProcess(args, 0, json.dumps(value), "")
        self.assertEqual(loop.Backend(runner=runner).validate_author("newauthor"), "NewAuthor")
        self.assertEqual(calls[0], ["gh", "api", "users/newauthor"])
        with self.assertRaises(loop.LoopError):
            loop.Backend(runner=runner).validate_author("../bad")
        def no_pr(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, json.dumps({"login": "Empty", "type": "User"} if args[1] == "api" else []), "")
        with self.assertRaises(loop.LoopError):
            loop.Backend(runner=no_pr).validate_author("Empty")


class StorageTests(unittest.TestCase):
    def setUp(self):
        # Keep even disposable test files inside this skill's owned directory.
        self.temp = tempfile.TemporaryDirectory(prefix=".pr-loop-test-", dir=Path(__file__).parent)
        self.directory = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_exported_token_passes_unchanged_and_scan_reports_name_only(self):
        exported = {"GH_TOKEN": "fixture-gh-export", "GITHUB_TOKEN": "fixture-github-export"}
        seen = []
        def runner(args, **kwargs):
            self.assertEqual(args[0], sys.executable)
            seen.append(kwargs["env"])
            return subprocess.CompletedProcess(args, 0, json.dumps({"requests": [], "user_id": ME}), "")
        reader = loop.Backend(runner=runner)
        backend = FakeBackend([pr()])
        backend.requests = reader.requests
        loop.atomic_json(self.directory / "config.json", loop.defaults([], "fixture", NOW))
        out = io.StringIO()
        with patch.dict(loop.os.environ, exported, clear=True), patch.object(loop, "Backend", return_value=backend), \
             patch.object(loop, "clock", return_value=NOW), contextlib.redirect_stdout(out):
            self.assertEqual(loop.main(["--state-dir", str(self.directory), "scan"]), 0)
        self.assertEqual(len(seen), 1)
        for name, value in exported.items():
            self.assertEqual(seen[0][name], value)
            self.assertIn(name, out.getvalue())
            self.assertNotIn(value, out.getvalue())
            self.assertNotIn(value, (self.directory / "state.json").read_text())
        self.assertIn("session-exported", out.getvalue())

    def test_next_report_default_summary_and_why_leave_state_untouched(self):
        config = loop.defaults([], "fixture", NOW)
        state = snapshot(pr(2566), pr(1, draft=True), pr(2, body="Parked. Do not test."),
                         pr(3, files=["scripts/build.py"]), pr(4, author="mooki000"))
        loop.take(state, config, 2566, EARLIER)
        loop.atomic_json(self.directory / "config.json", config)
        loop.atomic_json(self.directory / "state.json", state)
        before = (self.directory / "state.json").read_bytes()
        for command in ("next", "report"):
            for verbose in (False, True):
                with self.subTest(command=command, why=verbose):
                    out = io.StringIO()
                    args = ["--state-dir", str(self.directory), command] + (["--why"] if verbose else [])
                    with contextlib.redirect_stdout(out), patch.object(loop, "clock", return_value=NOW), \
                         patch.object(loop.Backend, "run", side_effect=AssertionError("no network reads")):
                        self.assertEqual(loop.main(args), 0)
                    text = out.getvalue()
                    if verbose:
                        self.assertIn("Exclude #1", text)
                        self.assertIn("Exclude #2", text)
                        self.assertIn("Exclude #3", text)
                        self.assertNotIn("4 excluded:", text)
                    else:
                        self.assertNotIn("Exclude #", text)
                        self.assertIn("4 excluded:", text)
                        for category in ("1 drafts without invitation", "1 awaiting diff review", "1 parked", "1 own/skipped authors"):
                            self.assertIn(category, text)
                        if command == "next":
                            self.assertEqual(len(text.splitlines()), 2)
                    self.assertEqual((self.directory / "state.json").read_bytes(), before)
                    self.assertEqual(loop.read_json(self.directory / "state.json")["active"]["2566"], state["active"]["2566"])

    def test_atomic_write_and_permissions(self):
        path = self.directory / "state.json"
        loop.atomic_json(path, loop.new_state())
        self.assertEqual(loop.read_json(path), loop.new_state())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual([p.name for p in self.directory.iterdir()], ["state.json"])

    def test_lock_refuses_second_process_and_releases(self):
        script = str(Path(loop.__file__))
        command = [sys.executable, "-I", "-B", script, "--state-dir", str(self.directory), "next"]
        with loop.locked(self.directory):
            result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn("holds the lock", result.stderr)
        self.assertFalse((self.directory / "config.json").exists())
        with loop.locked(self.directory):
            pass

    def test_corrupt_state_is_refused_not_overwritten(self):
        path = self.directory / "state.json"
        for bad in ('{"active":', '{"version":1,"active":{}}'):
            path.write_text(bad, encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stderr(out), patch.object(loop, "clock", side_effect=AssertionError("must refuse before clock/network")):
                status = loop.main(["--state-dir", str(self.directory), "scan"])
            self.assertEqual(status, 2)
            self.assertIn("corrupt", out.getvalue())
            self.assertEqual(path.read_text(), bad)
            self.assertFalse((self.directory / "config.json").exists())

    def test_failed_replace_leaves_original_state(self):
        path = self.directory / "state.json"
        loop.atomic_json(path, {"original": True})
        with patch.object(loop.os, "replace", side_effect=OSError("fixture interrupted replace")):
            with self.assertRaises(OSError):
                loop.atomic_json(path, {"original": False})
        self.assertEqual(loop.read_json(path), {"original": True})
        self.assertEqual(len(list(self.directory.iterdir())), 1)

    def test_request_reader_refuses_implicit_join_and_config_save(self):
        scripts = self.directory / "scripts"
        scripts.mkdir()
        (scripts / "team_chat.py").write_text(
            'class ChatError(Exception): pass\n'
            'def request(*a, **k): raise AssertionError("network must not be reached")\n'
            'def save(*a): raise AssertionError("save must not be reached")\n'
            'def load(): return {"user_id": "@test:example.invalid"}\n')
        for mutation in ('team_chat.request("POST", "join")', 'team_chat.save({})'):
            (scripts / "test_requests.py").write_text('import team_chat\ndef main(args):\n    ' + mutation + '\n')
            result = subprocess.run([sys.executable, "-I", "-B", "-c", loop.REQUEST_READER], cwd=self.directory,
                                    capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("read-only loop refuses", result.stderr)
            self.assertNotIn("must not be reached", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
