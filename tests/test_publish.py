"""Publisher state transitions exercised without private data or network access."""

import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from publisher.publish import PublishError, load_config, publication_lock, publish, run_command, source_revision


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / ".local/publication/catalog.json"
        self.config = {"intelligence_cli": "/private/cli", "intelligence_data_dir": "/private/data",
                       "output_file": str(self.output), "project_name": "zilnon-open"}
        self.catalog = {"schema_version": "1.0.0", "published_at": "2026-11-09T00:00:00Z",
                        "watchlist": [{"symbol": "MU", "name": "Micron"}], "reports": []}
        self.calls = []
        self.failure = None
        self.revision = patch("publisher.publish.source_revision", return_value="commit-one").start()
        self.addCleanup(patch.stopall)
        patch("publisher.publish.shutil.which", return_value="/bin/npm").start()
        patch("publisher.publish.run_command", side_effect=self.command).start()

    def command(self, command, *, cwd, env, timeout, stage):
        self.calls.append((stage, command, env))
        if stage == self.failure:
            raise PublishError("Synthetic stage failure")
        if stage == "Collection":
            self.output.parent.mkdir(parents=True, exist_ok=True)
            self.output.write_text(json.dumps(self.catalog))
        if stage == "Build":
            (self.root / "dist").mkdir(exist_ok=True)
            (self.root / "dist/index.html").write_text("synthetic static homepage")

    def run_publish(self, deploy=False):
        return publish(self.config, deploy=deploy, root=self.root)

    def stages(self):
        return [item[0] for item in self.calls]

    def state(self):
        return json.loads((self.root / ".local/publication-state.json").read_text())

    def test_local_default_unchanged_then_explicit_deploy(self):
        self.run_publish()
        self.run_publish()
        self.run_publish(deploy=True)
        self.run_publish(deploy=True)
        self.assertEqual(self.stages(), ["Collection", "Build", "Collection", "Collection", "Build", "Deployment", "Collection"])
        deployment = next(command for stage, command, _ in self.calls if stage == "Deployment")
        self.assertEqual(deployment, [str(self.root / "node_modules/.bin/wrangler"), "pages", "deploy", "dist",
                                     "--project-name", "zilnon-open", "--branch", "main", "--force"])
        self.assertEqual(self.state()["deployed"]["platform"], "cloudflare-pages")
        self.assertEqual((self.root / ".local/publication-state.json").stat().st_mode & 0o777, 0o600)

    def test_worker_deployment_state_does_not_skip_first_pages_upload(self):
        self.run_publish()
        state = self.state()
        state["deployed"] = {"version": state["built"], "project_name": self.config["project_name"],
                             "platform": "workers-static-assets"}
        (self.root / ".local/publication-state.json").write_text(json.dumps(state))
        self.calls.clear()
        self.run_publish(deploy=True)
        self.assertEqual(self.stages(), ["Collection", "Build", "Deployment"])
        self.assertEqual(self.state()["deployed"]["platform"], "cloudflare-pages")
        self.calls.clear()
        self.run_publish(deploy=True)
        self.assertEqual(self.stages(), ["Collection"])

    def test_pending_deployment_rebuilds_after_external_demo_build(self):
        self.run_publish()
        homepage = self.root / "dist/index.html"
        homepage.write_text("external demo build")
        self.calls.clear()
        self.run_publish(deploy=True)
        self.assertEqual(self.stages(), ["Collection", "Build", "Deployment"])
        self.assertEqual(homepage.read_text(), "synthetic static homepage")
        self.calls.clear()
        self.run_publish(deploy=True)
        self.assertEqual(self.stages(), ["Collection"])

    def test_dirty_source_is_never_cached_as_clean_revision(self):
        self.revision.return_value = None
        self.run_publish(deploy=True)
        self.run_publish(deploy=True)
        self.assertEqual(self.stages(), ["Collection", "Build", "Deployment"] * 2)
        self.assertIsNone(self.state()["built"]["source_revision"])

    def test_changed_content_and_source_revision_rebuild(self):
        self.run_publish(deploy=True)
        self.catalog["watchlist"][0]["name"] = "Micron Technology"
        self.run_publish(deploy=True)
        self.revision.return_value = "commit-two"
        self.run_publish(deploy=True)
        self.assertEqual(self.stages(), ["Collection", "Build", "Deployment"] * 3)
        self.assertEqual(self.state()["built"]["source_revision"], "commit-two")

    def test_failed_deploy_retries_with_fresh_build(self):
        self.run_publish(deploy=True)
        prior = self.state()["deployed"]
        self.catalog["watchlist"][0]["name"] = "Updated public name"
        self.failure = "Deployment"
        with self.assertRaises(PublishError):
            self.run_publish(deploy=True)
        self.assertEqual(self.state()["deployed"], prior)
        self.calls.clear()
        self.failure = None
        self.run_publish(deploy=True)
        self.assertEqual(self.stages(), ["Collection", "Build", "Deployment"])

    def test_build_failure_invalidates_artifacts_and_preserves_deployment(self):
        self.run_publish(deploy=True)
        prior = self.state()["deployed"]
        self.catalog["watchlist"][0]["name"] = "Changed"
        self.failure = "Build"
        with self.assertRaises(PublishError):
            self.run_publish(deploy=True)
        self.assertEqual(self.state()["deployed"], prior)
        self.assertIsNone(self.state()["built"])
        # Even reverting the report must rebuild potentially partial dist output.
        self.catalog["watchlist"][0]["name"] = "Micron"
        self.failure = None
        self.calls.clear()
        self.run_publish(deploy=True)
        self.assertEqual(self.stages(), ["Collection", "Build"])

    def test_collection_failure_and_extra_private_field_stop_build(self):
        self.run_publish(deploy=True)
        prior = self.state()
        self.failure = "Collection"
        with self.assertRaises(PublishError):
            self.run_publish(deploy=True)
        self.assertEqual(self.state(), prior)
        self.failure = None
        self.catalog["private_account"] = "must never publish"
        self.calls.clear()
        with self.assertRaises(PublishError):
            self.run_publish(deploy=True)
        self.assertEqual(self.stages(), ["Collection"])
        self.assertEqual(self.state(), prior)

    def test_concurrent_run_is_rejected(self):
        with publication_lock(self.root / ".local"):
            with self.assertRaises(PublishError):
                self.run_publish(deploy=True)
        self.assertEqual(self.calls, [])

    def test_build_does_not_receive_hosting_token(self):
        with patch.dict(os.environ, {"CLOUDFLARE_API_TOKEN": "synthetic-do-not-log"}):
            self.run_publish(deploy=True)
        build = next(env for stage, _, env in self.calls if stage == "Build")
        self.assertNotIn("CLOUDFLARE_API_TOKEN", build)
        self.assertEqual(build["ASTRO_TELEMETRY_DISABLED"], "1")
        self.assertEqual(build["ZILNON_PUBLICATION_FILE"], str(self.output))

    def test_config_rejects_relative_paths_and_secret_fields(self):
        config_path = self.root / "publisher.json"
        for invalid in ({**self.config, "api_token": "never-store"}, {**self.config, "output_file": "relative.json"}):
            config_path.write_text(json.dumps(invalid))
            with self.assertRaises(PublishError):
                load_config(config_path)


class CommandTests(unittest.TestCase):
    def test_dirty_git_status_has_no_cacheable_revision(self):
        with patch("publisher.publish.subprocess.run", return_value=SimpleNamespace(stdout=" M web/src/page.astro\n")) as command:
            self.assertIsNone(source_revision(Path.cwd()))
        self.assertEqual(command.call_count, 1)
        self.assertEqual(command.call_args.args[0], ["git", "status", "--porcelain"])

    def test_clean_git_status_returns_existing_revision(self):
        with patch("publisher.publish.subprocess.run", side_effect=[SimpleNamespace(stdout=""), SimpleNamespace(stdout="commit-one\n")]):
            self.assertEqual(source_revision(Path.cwd()), "commit-one")

    def test_failure_hides_child_output(self):
        with self.assertRaises(PublishError) as error:
            run_command(["/bin/sh", "-c", "echo synthetic-private-payload >&2; exit 1"], cwd=Path.cwd(),
                        env={}, timeout=2, stage="Collection")
        self.assertNotIn("synthetic-private-payload", str(error.exception))


if __name__ == "__main__":
    unittest.main()
