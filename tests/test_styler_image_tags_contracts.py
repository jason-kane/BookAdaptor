"""Characterize the input/output boundary of image-tag scheduling and analysis."""

import hashlib
import json
import sys
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from styler import image_tags
from styler.jobs import sample_task


class ImageTagContractTests(unittest.TestCase):
    """Exercise file identity, queue decisions, and observable job results."""

    def make_task(self, style="artist", engine="qwen", **values):
        task = sample_task(dict(style_tag=style, engine=engine, **values))
        return task

    @contextmanager
    def run_job_mocks(self, folder, analysis):
        """Replace service boundaries while leaving worker decisions real."""
        style = MagicMock()
        style.name = "Artist"
        style.engine_tier_badge.return_value = "tier badge"
        style_module = SimpleNamespace(Style=lambda _: style)
        const = SimpleNamespace(LIBRARY_DIR=folder)
        matching = {
            "tags": analysis.get("tags", []) if isinstance(analysis, dict) else analysis,
            "raw_tags": analysis.get("tags", []) if isinstance(analysis, dict) else analysis,
            "tag_matches": [],
        }
        import styler.sample_metadata as metadata
        import styler.tag_consensus as consensus
        import tag_matching

        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {
                "const": const, "styler.styles": style_module,
            }))
            stack.enter_context(patch.object(image_tags, "_current_style_description",
                                             return_value="historical style"))
            stack.enter_context(patch.object(image_tags, "analyze", return_value=analysis))
            publish = stack.enter_context(patch.object(image_tags, "publish_queue_event"))
            stack.enter_context(patch.object(consensus, "style_key", return_value="artist-key"))
            stack.enter_context(patch.object(consensus, "prompt_version", return_value="prompt-v1"))
            stack.enter_context(patch.object(consensus, "reconcile", return_value={
                "tags": [], "added": [], "removed": [], "counts": [],
            }))
            stack.enter_context(patch.object(tag_matching, "match_tags", return_value=matching))
            stack.enter_context(patch.object(metadata, "write_metadata"))
            stack.enter_context(patch.object(metadata, "image_tags", return_value=matching["tags"]))
            client = MagicMock()
            client.get.return_value = None
            client.sismember.return_value = False
            client.scard.return_value = 2
            yield client, style, publish

    def test_sample_paths_and_content_fingerprints_are_stable(self):
        with TemporaryDirectory() as folder:
            (Path(folder) / "style_exploration").mkdir()
            classic = image_tags.sample_image_path(folder, "artist", "qwen")
            child = image_tags.sample_image_path(
                folder, "artist", "qwen", "kid_friendly"
            )
            classic.write_bytes(b"sample")
            self.assertEqual(classic, Path(folder) / "style_exploration/artist_qwen.png")
            self.assertEqual(child, Path(folder) / "style_exploration/kid_friendly/artist_qwen.png")
            self.assertEqual(image_tags.fingerprint(classic), hashlib.sha256(b"sample").hexdigest())

    def test_queue_pop_can_select_a_style_before_fifo_fallback(self):
        client = MagicMock()
        client.eval.return_value = json.dumps({"style_tag": "artist"})
        self.assertEqual(image_tags.pop_tag_job(client, "jobs", "artist"), {"style_tag": "artist"})
        self.assertEqual(client.eval.call_args.args[-1], "artist")
        client.eval.return_value = None
        self.assertIsNone(image_tags.pop_tag_job(client, "jobs"))

    def test_analysis_schema_maps_legacy_placement_and_rejects_invalid_results(self):
        self.assertEqual(image_tags.parse_analysis(
            '{"tags":["Oil Painting"],"character_placement":"in_front"}'
        ), {"tags": ["oil painting"], "character_placement": "outside_image"})
        self.assertEqual(image_tags.parse_analysis(
            '{"tags":[],"character_placement":"uncertain"}'
        ), {"tags": [], "character_placement": "uncertain"})
        for result in (
            '["oil painting"]',
            '{"tags":[],"character_placement":"unknown"}',
            '{"tags":["oil painting"]}',
        ):
            with self.subTest(result=result), self.assertRaises(ValueError):
                image_tags.parse_analysis(result)

    def test_output_parser_handles_escaped_quotes_and_rejects_malformed_payloads(self):
        self.assertEqual(image_tags.parse_tags(r'{"tags":["quoted \"word\""]}'),
                         ['quoted "word"'])
        for response in (
            "no structured output", "{'tags': [broken]}", '{"tags":["unfinished"',
        ):
            with self.subTest(response=response), self.assertRaises(ValueError):
                image_tags.tag_payload(response)

    def test_schedule_skips_merging_abyss_and_current_cached_records(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "sample.png"
            path.write_bytes(b"sample")
            task = self.make_task()
            client = MagicMock()
            with patch("styler.style_duplicates.style_merge_locked", return_value=True):
                self.assertEqual(image_tags.schedule(client, task, path), [])
            with patch("styler.style_duplicates.style_merge_locked", return_value=False), \
                    patch("styler.tag_consensus.style_key", return_value="artist-key"):
                client.sismember.return_value = True
                self.assertEqual(image_tags.schedule(client, task, path), [])
                client.sismember.return_value = False
                client.get.return_value = json.dumps({
                    "sha256": image_tags.fingerprint(path), "tags": ["oil painting"],
                    "analysis_version": image_tags.ANALYSIS_VERSION,
                })
                self.assertEqual(image_tags.schedule(client, task, path), ["oil painting"])
            client.set.assert_not_called()

    def test_schedule_enqueues_and_priority_promotes_without_duplicate_submission(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "sample.png"
            path.write_bytes(b"sample")
            task = self.make_task(base_style_prompt="kid_friendly")
            client = MagicMock()
            client.sismember.return_value = False
            client.get.return_value = None
            client.set.return_value = True
            with patch("styler.style_duplicates.style_merge_locked", return_value=False), \
                    patch("styler.tag_consensus.prompt_version", return_value="prompt-v1"):
                image_tags.schedule(client, task, path)
                queued = json.loads(client.rpush.call_args.args[1])
                self.assertEqual(queued["base_style_prompt"], "kid_friendly")
                self.assertEqual(queued["analysis_version"], image_tags.ANALYSIS_VERSION)
                client.eval.return_value = 1
                image_tags.schedule(client, task, path, force=True, priority=True)
            self.assertEqual(client.publish.call_count, 2)
            self.assertTrue(json.loads(client.publish.call_args_list[-1].args[1])["priority"])

    def test_schedule_preserves_deduplication_and_cleans_failed_pending_markers(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "sample.png"
            path.write_bytes(b"sample")
            task = self.make_task()
            with patch("styler.style_duplicates.style_merge_locked", return_value=False), \
                    patch("styler.tag_consensus.prompt_version", return_value="prompt-v1"):
                client = MagicMock()
                client.sismember.return_value = False
                client.get.return_value = None
                client.set.return_value = False
                image_tags.schedule(client, task, path)
                client.rpush.assert_not_called()

                client.set.return_value = True
                client.rpush.side_effect = OSError("Redis unavailable")
                with self.assertRaisesRegex(OSError, "Redis unavailable"):
                    image_tags.schedule(client, task, path)
                client.delete.assert_called_once()

                client.rpush.side_effect = None
                client.eval.return_value = 0
                client.exists.return_value = True
                with self.assertLogs(image_tags.log, level="WARNING"):
                    image_tags.schedule(client, task, path, priority=True)
                self.assertEqual(client.publish.call_count, 0)

    def test_schedule_requeues_old_versions_and_does_not_warn_when_idle(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "sample.png"
            path.write_bytes(b"sample")
            task = self.make_task()
            task.pop("base_style_prompt")
            client = MagicMock()
            client.sismember.return_value = False
            client.get.return_value = json.dumps({
                "sha256": image_tags.fingerprint(path), "tags": ["ink"],
                "analysis_version": "older-version",
            })
            client.set.return_value = True
            with patch("styler.style_duplicates.style_merge_locked", return_value=False), \
                    patch("styler.tag_consensus.prompt_version", return_value="prompt-v2"):
                image_tags.schedule(client, task, path)
            queued = json.loads(client.rpush.call_args.args[1])
            self.assertNotIn("base_style_prompt", queued)
            self.assertEqual(queued["analysis_version"], image_tags.ANALYSIS_VERSION)

            client.reset_mock()
            client.sismember.return_value = False
            client.get.return_value = None
            client.eval.return_value = 0
            client.exists.return_value = False
            with patch("styler.style_duplicates.style_merge_locked", return_value=False), \
                    patch("styler.tag_consensus.prompt_version", return_value="prompt-v2"):
                image_tags.schedule(client, task, path, priority=True)
            client.publish.assert_not_called()

    def test_retag_requests_validate_engine_and_prioritize_existing_files(self):
        with TemporaryDirectory() as folder:
            sample = Path(folder) / "style_exploration"
            sample.mkdir()
            (sample / "artist_zit.png").write_bytes(b"zit")
            (sample / "artist_qwen.png").write_bytes(b"qwen")
            client = MagicMock()
            client.sismember.return_value = False
            with patch("styler.style_duplicates.style_merge_locked", return_value=False), \
                    patch.object(image_tags, "schedule") as schedule:
                tasks = image_tags.request_retag(client, "artist", folder)
            self.assertEqual([task["engine"] for task in tasks], ["zit", "qwen"])
            self.assertTrue(all(call.kwargs == {"force": True, "priority": True}
                                for call in schedule.call_args_list))
            with (
                patch("styler.style_duplicates.style_merge_locked", return_value=False),
                self.assertRaisesRegex(ValueError, "Unknown sample engine"),
            ):
                image_tags.request_retag(client, "artist", folder, engine="unknown")
            with patch("styler.style_duplicates.style_merge_locked", return_value=False), \
                    patch.object(image_tags, "schedule"):
                explicit = image_tags.request_retag(client, "artist", folder, engine="qwen")
            self.assertEqual([task["engine"] for task in explicit], ["qwen"])

    def test_retag_request_rejects_merge_and_abyss_and_absent_samples(self):
        client = MagicMock()
        with (
            patch("styler.style_duplicates.style_merge_locked", return_value=True),
            self.assertRaisesRegex(ValueError, "being merged"),
        ):
            image_tags.request_retag(client, "artist", "/missing")
        with patch("styler.style_duplicates.style_merge_locked", return_value=False), \
                patch("styler.tag_consensus.style_key", return_value="artist-key"):
            client.sismember.return_value = True
            with self.assertRaisesRegex(ValueError, "ABYSS"):
                image_tags.request_retag(client, "artist", "/missing")
            client.sismember.return_value = False
            with self.assertRaisesRegex(ValueError, "No sample images"):
                image_tags.request_retag(client, "artist", "/missing")

    def test_missing_and_visible_tag_requests_schedule_only_needed_samples(self):
        with TemporaryDirectory() as folder:
            directory = Path(folder) / "style_exploration"
            directory.mkdir()
            kid_friendly = directory / "kid_friendly"
            kid_friendly.mkdir()
            for engine in ("zit", "krea2", "qwen"):
                (directory / f"artist_{engine}.png").write_bytes(engine.encode())
                (kid_friendly / f"artist_{engine}.png").write_bytes(engine.encode())
            client = MagicMock()
            client.sismember.return_value = False
            client.set.side_effect = [True] * 6 + [False] * 6
            with patch("styler.style_duplicates.style_merge_locked", return_value=False), \
                    patch("styler.sample_metadata.image_tags", return_value=[]), \
                    patch.object(image_tags, "schedule") as schedule:
                missing = image_tags.request_missing_tags(client, "artist", folder)
                self.assertEqual(len(missing), 6)  # Both supported base prompts.
                self.assertTrue(image_tags.auto_retag_if_empty(client, "artist", folder))
                self.assertFalse(image_tags.auto_retag_if_empty(client, "artist", folder))
                prioritized = image_tags.prioritize_style_tags(client, "artist", folder)
            self.assertEqual(len(prioritized), 6)
            self.assertGreaterEqual(schedule.call_count, 18)

    def test_auto_retag_skips_abyss_and_clears_its_marker_when_enqueue_fails(self):
        client = MagicMock()
        client.sismember.return_value = True
        self.assertFalse(image_tags.auto_retag_if_empty(client, "artist", "/library"))

        with TemporaryDirectory() as folder:
            sample_dir = Path(folder) / "style_exploration"
            sample_dir.mkdir()
            (sample_dir / "artist_qwen.png").write_bytes(b"sample")
            client.sismember.return_value = False
            client.set.return_value = True
            with (
                patch("styler.sample_metadata.image_tags", return_value=[]),
                patch.object(image_tags, "schedule", side_effect=RuntimeError("queue error")),
                self.assertRaisesRegex(RuntimeError, "queue error"),
            ):
                image_tags.auto_retag_if_empty(client, "artist", folder)
            client.delete.assert_called_once_with("styler:auto-retag:artist:classic:qwen")
            (sample_dir / "artist_qwen.png").unlink()
            self.assertEqual(image_tags.prioritize_style_tags(client, "artist", folder), [])

    def test_auto_retag_ignores_images_that_already_have_display_tags(self):
        with TemporaryDirectory() as folder:
            directory = Path(folder) / "style_exploration"
            directory.mkdir()
            (directory / "artist_qwen.png").write_bytes(b"sample")
            client = MagicMock()
            client.sismember.return_value = False
            with patch("styler.sample_metadata.image_tags", return_value=["ink"]):
                self.assertFalse(image_tags.auto_retag_if_empty(client, "artist", folder))
            client.set.assert_not_called()

    def test_analysis_record_requires_matching_digest_version_and_nonempty_tags(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "sample.png"
            path.write_bytes(b"sample")
            task = self.make_task()
            digest = image_tags.fingerprint(path)
            client = MagicMock()
            client.get.return_value = json.dumps({
                "sha256": digest, "analysis_version": image_tags.ANALYSIS_VERSION,
                "tags": ["ink"],
            })
            self.assertTrue(image_tags._analysis_record_current(client, task, path))
            client.get.return_value = b"not-json"
            self.assertFalse(image_tags._analysis_record_current(client, task, path))
            client.get.return_value = "[]"
            self.assertFalse(image_tags._analysis_record_current(client, task, path))
            client.get.return_value = json.dumps({"sha256": digest, "tags": ["ink"]})
            self.assertFalse(image_tags._analysis_record_current(client, task, path))
            client.get.return_value = json.dumps({
                "sha256": digest, "analysis_version": image_tags.ANALYSIS_VERSION, "tags": [],
            })
            self.assertFalse(image_tags._analysis_record_current(client, task, path))

    def test_requests_for_missing_tags_skip_merge_and_abyss_styles(self):
        client = MagicMock()
        with (
            patch("styler.style_duplicates.style_merge_locked", return_value=True),
            self.assertRaisesRegex(ValueError, "being merged"),
        ):
            image_tags.request_missing_tags(client, "artist", "/library")
        with patch("styler.style_duplicates.style_merge_locked", return_value=False), \
                patch("styler.tag_consensus.style_key", return_value="artist-key"):
            client.sismember.return_value = True
            self.assertEqual(image_tags.request_missing_tags(client, "artist", "/library"), [])

    def test_missing_tags_requeue_stale_analysis_even_when_metadata_has_tags(self):
        with TemporaryDirectory() as folder:
            directory = Path(folder) / "style_exploration"
            directory.mkdir()
            (directory / "artist_qwen.png").write_bytes(b"sample")
            client = MagicMock()
            client.sismember.return_value = False
            with patch("styler.style_duplicates.style_merge_locked", return_value=False), \
                    patch("styler.sample_metadata.image_tags", return_value=["ink"]), \
                    patch.object(image_tags, "schedule") as schedule:
                tasks = image_tags.request_missing_tags(client, "artist", folder)
            self.assertEqual([task["engine"] for task in tasks], ["qwen"])
            schedule.assert_called_once()

    def test_reconciliation_stops_for_abyss_or_missing_current_evidence(self):
        client = MagicMock()
        client.sismember.side_effect = [False, True]
        self.assertIsNone(image_tags.reconcile_style_on_detail(client, "artist", "/library"))
        client.sismember.side_effect = [False, False]
        with patch("styler.sample_metadata.image_tags", return_value=[]):
            self.assertIsNone(image_tags.reconcile_style_on_detail(client, "artist", "/library"))

    def test_current_style_description_requires_its_signature_to_match(self):
        style = SimpleNamespace(style={
            "historical_description": "A concise description",
            "historical_description_signature": "signature",
        })
        with patch("styler.style_description._source", return_value=(None, "signature")):
            self.assertEqual(image_tags._current_style_description(style), "A concise description")
        with patch("styler.style_description._source", return_value=(None, "new-signature")):
            self.assertEqual(image_tags._current_style_description(style), "")
        self.assertEqual(image_tags._current_style_description(SimpleNamespace(style={})), "")

    def test_detail_reconciliation_waits_for_all_current_engine_records(self):
        with TemporaryDirectory() as folder:
            classic = Path(folder) / "style_exploration"
            kid = classic / "kid_friendly"
            kid.mkdir(parents=True)
            for directory in (classic, kid):
                for engine in ("zit", "krea2", "qwen"):
                    (directory / f"artist_{engine}.png").write_bytes(engine.encode())
            client = MagicMock()
            client.sismember.return_value = False
            client.scard.side_effect = [1, 2]
            with patch("styler.sample_metadata.image_tags", return_value=["ink"]), \
                    patch.object(image_tags, "_analysis_record_current", return_value=True), \
                    patch("styler.tag_consensus.reconcile", return_value={
                        "tags": ["ink"], "added": ["ink"], "removed": [], "counts": [],
                    }) as reconcile:
                result = image_tags.reconcile_style_on_detail(client, "artist", folder)
            self.assertEqual(result["state"], "style_tags_reconciled")
            self.assertEqual(result["tags"], ["ink"])
            reconcile.assert_called_once()

    def test_detail_reconciliation_resolves_lost_styles_and_ignores_empty_changes(self):
        with TemporaryDirectory() as folder:
            classic = Path(folder) / "style_exploration"
            kid = classic / "kid_friendly"
            kid.mkdir(parents=True)
            for directory in (classic, kid):
                for engine in ("zit", "krea2", "qwen"):
                    (directory / f"artist_{engine}.png").write_bytes(engine.encode())
            agreement = {"tags": ["ink"], "added": ["ink"], "removed": [], "counts": []}
            client = MagicMock()
            client.sismember.return_value = False
            client.scard.side_effect = [1, 4]
            with patch("styler.sample_metadata.image_tags", return_value=["ink"]), \
                    patch.object(image_tags, "_analysis_record_current", return_value=True), \
                    patch("styler.tag_consensus.reconcile", return_value=agreement):
                image_tags.reconcile_style_on_detail(client, "artist", folder)

            client.sismember.side_effect = [True, False]
            client.scard.side_effect = [1, 4]
            with patch("styler.sample_metadata.image_tags", return_value=["ink"]), \
                    patch.object(image_tags, "_analysis_record_current", return_value=True), \
                    patch("styler.tag_consensus.reconcile", return_value=agreement):
                resolved = image_tags.reconcile_style_on_detail(client, "artist", folder)
            self.assertEqual(resolved["state"], "lost_resolved")

            client.sismember.side_effect = None
            client.sismember.return_value = False
            client.scard.side_effect = [1, 4]
            with patch("styler.sample_metadata.image_tags", return_value=["ink"]), \
                    patch.object(image_tags, "_analysis_record_current", return_value=True), \
                    patch("styler.tag_consensus.reconcile", return_value={
                        "tags": ["ink"], "added": [], "removed": [], "counts": [],
                    }):
                self.assertIsNone(image_tags.reconcile_style_on_detail(client, "artist", folder))

    def test_run_job_persists_image_record_and_reports_engine_tier(self):
        with TemporaryDirectory() as folder:
            sample_dir = Path(folder) / "style_exploration"
            sample_dir.mkdir()
            path = sample_dir / "artist_qwen.png"
            path.write_bytes(b"sample")
            task = self.make_task()
            task["sha256"] = image_tags.fingerprint(path)
            with self.run_job_mocks(folder, {
                "tags": ["oil on canvas"], "character_placement": "outside_image",
            }) as (client, style, publish):
                image_tags.run_job(client, task)

            record = json.loads(path.with_suffix(".tags.json").read_text())
            self.assertEqual(record["tags"], ["oil on canvas"])
            self.assertEqual(record["image_tier"], 1)
            style.set_engine_tier.assert_called_once_with("qwen", 1, source="image-analysis")
            event = publish.call_args.args[2]
            self.assertEqual(event["state"], "prompt_tagged")
            self.assertEqual(event["tier_badge_html"], "tier badge")
            client.delete.assert_called_once()

    def test_run_job_emits_failure_and_releases_lock_for_missing_sample(self):
        with TemporaryDirectory() as folder:
            task = self.make_task()
            task["sha256"] = "expected-digest"
            with self.run_job_mocks(folder, ["unused"]) as (client, _, publish):
                image_tags.run_job(client, task)
            event = publish.call_args.args[2]
            self.assertEqual(event["state"], "tag_failed")
            self.assertIn("no longer exists", event["message"])
            client.delete.assert_called_once()

    def test_run_job_abstains_for_abyss_and_requeues_changed_images(self):
        with TemporaryDirectory() as folder:
            task = self.make_task()
            task["sha256"] = "expected-digest"
            with self.run_job_mocks(folder, ["unused"]) as (client, _, publish):
                client.sismember.return_value = True
                image_tags.run_job(client, task)
            self.assertEqual(publish.call_args.args[2]["state"], "tag_abyss")
            client.delete.assert_called_once()

        with TemporaryDirectory() as folder:
            sample_dir = Path(folder) / "style_exploration"
            sample_dir.mkdir()
            path = sample_dir / "artist_qwen.png"
            path.write_bytes(b"replacement")
            task = self.make_task()
            task["sha256"] = "older-digest"
            with self.run_job_mocks(folder, ["unused"]) as (client, _, publish), \
                    patch.object(image_tags, "schedule") as schedule:
                image_tags.run_job(client, task)
            schedule.assert_called_once()
            self.assertEqual(publish.call_args.args[2]["state"], "tag_failed")
            client.delete.assert_called_once()

    def test_run_job_defers_until_historical_description_is_ready(self):
        with TemporaryDirectory() as folder:
            sample_dir = Path(folder) / "style_exploration"
            sample_dir.mkdir()
            path = sample_dir / "artist_qwen.png"
            path.write_bytes(b"sample")
            task = self.make_task(priority=True)
            task["priority"] = True
            task["sha256"] = image_tags.fingerprint(path)
            with self.run_job_mocks(folder, ["unused"]) as (client, _, _), \
                    patch.object(image_tags, "_current_style_description", return_value=""), \
                    patch("styler.style_description.request", return_value={
                        "state": "queued", "job_id": "description-job",
                    }):
                image_tags.run_job(client, task)
            resumed = json.loads(client.rpush.call_args.args[1])
            self.assertEqual(resumed["description_waited"], True)
            self.assertEqual(client.rpush.call_args.args[0], image_tags.PRIORITY_QUEUE)
            client.delete.assert_not_called()

    def test_run_job_retains_pending_marker_if_dependency_publish_fails_after_enqueue(self):
        with TemporaryDirectory() as folder:
            sample_dir = Path(folder) / "style_exploration"
            sample_dir.mkdir()
            path = sample_dir / "artist_qwen.png"
            path.write_bytes(b"sample")
            task = self.make_task()
            task["sha256"] = image_tags.fingerprint(path)
            with self.run_job_mocks(folder, ["unused"]) as (client, _, _), \
                    patch.object(image_tags, "_current_style_description", return_value=""), \
                    patch("styler.style_description.request", return_value={"state": "queued"}):
                client.publish.side_effect = [None, RuntimeError("subscriber unavailable")]
                image_tags.run_job(client, task)
            client.rpush.assert_called_once()
            client.delete.assert_not_called()

    def test_run_job_uses_legacy_kid_friendly_identity_and_rejects_bad_ids(self):
        with TemporaryDirectory() as folder:
            classic_dir = Path(folder) / "style_exploration"
            classic_dir.mkdir()
            (classic_dir / "artist_qwen.png").write_bytes(b"classic image")
            task = self.make_task(base_style_prompt="kid_friendly")
            task.pop("base_style_prompt")
            task["sha256"] = "expected-digest"
            with self.run_job_mocks(folder, []) as (client, _, publish):
                image_tags.run_job(client, task)
            event = publish.call_args.args[2]
            self.assertEqual(event["base_style_prompt"], "kid_friendly")
            self.assertIn("no longer exists", event["message"])

        with TemporaryDirectory() as folder:
            task = self.make_task()
            task["job_id"] = "invalid-id"
            with (
                patch.dict(sys.modules, {"const": SimpleNamespace(LIBRARY_DIR=folder)}),
                self.assertRaisesRegex(ValueError, "Invalid sample identity"),
            ):
                image_tags.run_job(MagicMock(), task)

            task = self.make_task()
            task.pop("base_style_prompt")
            task["job_id"] = "invalid-legacy-id"
            with (
                patch.dict(sys.modules, {"const": SimpleNamespace(LIBRARY_DIR=folder)}),
                self.assertRaisesRegex(ValueError, "Invalid sample identity"),
            ):
                image_tags.run_job(MagicMock(), task)

    def test_run_job_updates_running_status_and_removes_previous_tag_indexes(self):
        with TemporaryDirectory() as folder:
            sample_dir = Path(folder) / "style_exploration"
            sample_dir.mkdir()
            path = sample_dir / "artist_qwen.png"
            path.write_bytes(b"sample")
            task = self.make_task()
            task["sha256"] = image_tags.fingerprint(path)
            running = json.dumps({"job_id": task["job_id"], "state": "running"})
            previous = json.dumps({"tags": ["old-tag"]})
            with self.run_job_mocks(folder, ["new-tag"]) as (client, _, publish):
                client.get.side_effect = [running, previous]
                image_tags.run_job(client, task)
            client.set.assert_called_once()
            pipeline = client.pipeline.return_value.__enter__.return_value
            pipeline.srem.assert_called_once_with("styler:image-tag:old-tag", task["job_id"])
            self.assertEqual(publish.call_args.args[2]["state"], "prompt_tagged")

    def test_run_job_does_not_update_a_different_or_invalid_running_record(self):
        with TemporaryDirectory() as folder:
            sample_dir = Path(folder) / "style_exploration"
            sample_dir.mkdir()
            path = sample_dir / "artist_qwen.png"
            path.write_bytes(b"sample")
            task = self.make_task()
            task["sha256"] = image_tags.fingerprint(path)
            for running in ('not-json', json.dumps({"job_id": "someone-else"})):
                with self.subTest(running=running), \
                        self.run_job_mocks(folder, ["tag"]) as (client, _, _):
                    client.get.side_effect = [running, None]
                    image_tags.run_job(client, task)
                    client.set.assert_not_called()

    def test_run_job_reports_completed_description_without_a_valid_signature(self):
        with TemporaryDirectory() as folder:
            sample_dir = Path(folder) / "style_exploration"
            sample_dir.mkdir()
            path = sample_dir / "artist_qwen.png"
            path.write_bytes(b"sample")
            task = self.make_task()
            task["sha256"] = image_tags.fingerprint(path)
            with self.run_job_mocks(folder, ["unused"]) as (client, _, publish), \
                    patch.object(image_tags, "_current_style_description", side_effect=["", ""]), \
                    patch("styler.style_description.request", return_value={"state": "complete"}):
                image_tags.run_job(client, task)
            event = publish.call_args.args[2]
            self.assertEqual(event["state"], "tag_failed")
            self.assertIn("without a current description", event["message"])

    def test_run_job_continues_when_dependency_completion_has_a_current_description(self):
        with TemporaryDirectory() as folder:
            sample_dir = Path(folder) / "style_exploration"
            sample_dir.mkdir()
            path = sample_dir / "artist_qwen.png"
            path.write_bytes(b"sample")
            task = self.make_task()
            task["sha256"] = image_tags.fingerprint(path)
            import styler.tag_consensus as consensus
            with self.run_job_mocks(folder, ["tag"]) as (client, _, publish), \
                    patch.object(image_tags, "_current_style_description",
                                 side_effect=["", "current description"]), \
                    patch("styler.style_description.request", return_value={"state": "complete"}), \
                    patch.object(consensus, "prompt_version", return_value="prompt-v1"):
                image_tags.run_job(client, task)
            self.assertEqual(publish.call_args.args[2]["state"], "prompt_tagged")

    def test_run_job_stops_after_a_prior_description_wait_was_unsuccessful(self):
        with TemporaryDirectory() as folder:
            sample_dir = Path(folder) / "style_exploration"
            sample_dir.mkdir()
            path = sample_dir / "artist_qwen.png"
            path.write_bytes(b"sample")
            task = self.make_task()
            task["description_waited"] = True
            task["sha256"] = image_tags.fingerprint(path)
            with self.run_job_mocks(folder, ["unused"]) as (client, _, publish), \
                    patch.object(image_tags, "_current_style_description", return_value=""):
                image_tags.run_job(client, task)
            event = publish.call_args.args[2]
            self.assertEqual(event["state"], "tag_failed")
            self.assertIn("description generation failed", event["message"])

    def test_run_job_requeues_when_the_image_or_prompt_changes_mid_analysis(self):
        with TemporaryDirectory() as folder:
            sample_dir = Path(folder) / "style_exploration"
            sample_dir.mkdir()
            path = sample_dir / "artist_qwen.png"
            path.write_bytes(b"sample")
            task = self.make_task()
            task["sha256"] = image_tags.fingerprint(path)
            import styler.tag_consensus as consensus
            with self.run_job_mocks(folder, ["tag"]) as (client, _, publish), \
                    patch.object(consensus, "prompt_version", side_effect=["prompt-v1", "prompt-v2"]), \
                    patch.object(image_tags, "schedule") as schedule:
                image_tags.run_job(client, task)
            schedule.assert_called_once()
            self.assertEqual(publish.call_args.args[2]["state"], "tag_failed")
            client.pipeline.assert_not_called()

    def test_empty_analysis_moves_lost_style_to_abyss(self):
        with TemporaryDirectory() as folder:
            sample_dir = Path(folder) / "style_exploration"
            sample_dir.mkdir()
            path = sample_dir / "artist_qwen.png"
            path.write_bytes(b"sample")
            task = self.make_task()
            task["sha256"] = image_tags.fingerprint(path)
            with self.run_job_mocks(folder, {"tags": []}) as (client, _, publish):
                client.sismember.side_effect = [False, True]
                client.scard.return_value = 0
                image_tags.run_job(client, task)
            self.assertEqual(publish.call_args.args[2]["state"], "tag_abyss")
            client.srem.assert_called_once_with("tags", "LOST")
            client.lrem.assert_called_once_with("categories", 0, "LOST")

    def test_empty_analysis_keeps_lost_category_when_other_lost_styles_remain(self):
        with TemporaryDirectory() as folder:
            sample_dir = Path(folder) / "style_exploration"
            sample_dir.mkdir()
            path = sample_dir / "artist_qwen.png"
            path.write_bytes(b"sample")
            task = self.make_task()
            task["sha256"] = image_tags.fingerprint(path)
            with self.run_job_mocks(folder, {"tags": []}) as (client, _, publish):
                client.sismember.side_effect = [False, True]
                client.scard.return_value = 2
                image_tags.run_job(client, task)
            self.assertEqual(publish.call_args.args[2]["state"], "tag_abyss")
            pipeline = client.pipeline.return_value.__enter__.return_value
            pipeline.srem.assert_called_with("LOST/tags", "artist-key")
            client.srem.assert_not_called()

    def test_analyze_runs_named_workflow_and_cleans_temporary_artifacts(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.png"
            source.write_bytes(b"source")
            inputs, outputs = root / "inputs", root / "outputs"
            inputs.mkdir()
            outputs.mkdir()

            def run_workflow(workflow, template_environment):
                (outputs / f"{template_environment['OUTPUT_PREFIX']}_result.txt").write_text("tags")

            comfy = SimpleNamespace(
                load_workflow_template=MagicMock(return_value=SimpleNamespace(workflow={})),
                free_vram=MagicMock(), run_workflow=run_workflow,
            )
            const = SimpleNamespace(COMFY_DIRS={"artifactserver": {
                "INPUT_DIR": str(inputs), "OUTPUT_DIR": str(outputs),
            }})
            with patch.dict(sys.modules, {"comfy": comfy, "const": const}):
                result = image_tags.analyze(
                    source, parser=lambda text: [text], style_name="artist",
                    style_description="historical appearance",
                )
            self.assertEqual(result, ["tags"])
            comfy.load_workflow_template.assert_called_once_with(
                "api", "i2json", "styler-image-tags"
            )
            self.assertEqual(list(inputs.iterdir()), [])
            self.assertEqual(list(outputs.iterdir()), [])

    def test_analyze_retries_empty_results_and_updates_the_description_prompt(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.png"
            source.write_bytes(b"source")
            inputs, outputs = root / "inputs", root / "outputs"
            inputs.mkdir()
            outputs.mkdir()
            workflow = SimpleNamespace(workflow={
                "other": {"class_type": "UnrelatedNode", "inputs": {}},
                "describe": {
                    "class_type": "Qwen2_5_VL_GGUF_Describe_Image",
                    "inputs": {"prompt": "Describe the image"},
                },
            })
            runs = []

            def run_workflow(current_workflow, template_environment):
                runs.append(current_workflow.workflow["describe"]["inputs"].copy())
                (outputs / f"{template_environment['OUTPUT_PREFIX']}_result.txt").write_text("result")

            comfy = SimpleNamespace(
                load_workflow_template=MagicMock(return_value=workflow),
                free_vram=MagicMock(), run_workflow=run_workflow,
            )
            const = SimpleNamespace(COMFY_DIRS={"artifactserver": {
                "INPUT_DIR": str(inputs), "OUTPUT_DIR": str(outputs),
            }})
            parser = MagicMock(side_effect=[[], ["oil painting"]])
            statuses = []
            with patch.dict(sys.modules, {"comfy": comfy, "const": const}):
                result = image_tags.analyze(source, parser=parser,
                                            on_status=lambda *event: statuses.append(event))
            self.assertEqual(result, ["oil painting"])
            self.assertIn("Reinspect the image carefully", runs[1]["prompt"])
            self.assertEqual(runs[1]["temperature"], 0.2)
            self.assertEqual(comfy.free_vram.call_count, 4)
            self.assertEqual(len(statuses), 8)
            self.assertEqual(list(inputs.iterdir()), [])
            self.assertEqual(list(outputs.iterdir()), [])

    def test_analyze_rejects_missing_outputs_after_one_retry(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.png"
            source.write_bytes(b"source")
            inputs, outputs = root / "inputs", root / "outputs"
            inputs.mkdir()
            outputs.mkdir()
            comfy = SimpleNamespace(
                load_workflow_template=MagicMock(return_value={}),
                free_vram=MagicMock(), run_workflow=MagicMock(),
            )
            const = SimpleNamespace(COMFY_DIRS={"artifactserver": {
                "INPUT_DIR": str(inputs), "OUTPUT_DIR": str(outputs),
            }})
            with (
                patch.dict(sys.modules, {"comfy": comfy, "const": const}),
                self.assertRaisesRegex(ValueError, "one text result"),
            ):
                image_tags.analyze(source)
            self.assertEqual(comfy.load_workflow_template.call_count, 2)
            self.assertEqual(comfy.free_vram.call_count, 4)
            self.assertEqual(list(inputs.iterdir()), [])

    def test_analyze_retries_a_parser_error_and_returns_the_second_result(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.png"
            source.write_bytes(b"source")
            inputs, outputs = root / "inputs", root / "outputs"
            inputs.mkdir()
            outputs.mkdir()

            def run_workflow(workflow, template_environment):
                (outputs / f"{template_environment['OUTPUT_PREFIX']}_result.txt").write_text("payload")

            comfy = SimpleNamespace(
                load_workflow_template=MagicMock(return_value={}),
                free_vram=MagicMock(), run_workflow=run_workflow,
            )
            const = SimpleNamespace(COMFY_DIRS={"artifactserver": {
                "INPUT_DIR": str(inputs), "OUTPUT_DIR": str(outputs),
            }})
            parser = MagicMock(side_effect=[ValueError("bad format"), {"tags": ["ink"]}])
            with patch.dict(sys.modules, {"comfy": comfy, "const": const}):
                result = image_tags.analyze(source, parser=parser)
            self.assertEqual(result, {"tags": ["ink"]})
            self.assertEqual(parser.call_count, 2)
            self.assertEqual(list(outputs.iterdir()), [])

    def test_analyze_propagates_parser_failure_after_its_single_retry(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.png"
            source.write_bytes(b"source")
            inputs, outputs = root / "inputs", root / "outputs"
            inputs.mkdir()
            outputs.mkdir()

            def run_workflow(workflow, template_environment):
                (outputs / f"{template_environment['OUTPUT_PREFIX']}_result.txt").write_text("payload")

            comfy = SimpleNamespace(
                load_workflow_template=MagicMock(return_value={}),
                free_vram=MagicMock(), run_workflow=run_workflow,
            )
            const = SimpleNamespace(COMFY_DIRS={"artifactserver": {
                "INPUT_DIR": str(inputs), "OUTPUT_DIR": str(outputs),
            }})
            parser = MagicMock(side_effect=ValueError("still invalid"))
            with (
                patch.dict(sys.modules, {"comfy": comfy, "const": const}),
                self.assertRaisesRegex(ValueError, "still invalid"),
            ):
                image_tags.analyze(source, parser=parser)
            self.assertEqual(parser.call_count, 2)
            self.assertEqual(list(outputs.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
