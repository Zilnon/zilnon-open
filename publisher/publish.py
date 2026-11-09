"""Collect an allowlisted archive, build locally, and explicitly deploy static files."""

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from .contracts import PublicationError, validate_publication

ROOT = Path(__file__).resolve().parents[1]


class PublishError(ValueError):
    """Diagnostics contain no private paths, payloads, or child output."""


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PublishError("Cannot read valid publisher JSON") from None


def load_config(path):
    if not path.is_absolute():
        raise PublishError("Configuration path must be absolute")
    value = read_json(path)
    keys = {"intelligence_cli", "intelligence_data_dir", "output_file", "project_name"}
    if not isinstance(value, dict) or set(value) != keys:
        raise PublishError("Configuration requires exactly the four documented fields")
    for key in keys - {"project_name"}:
        if not isinstance(value[key], str) or not Path(value[key]).is_absolute():
            raise PublishError("Configured filesystem paths must be absolute")
    if not isinstance(value["project_name"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,57}", value["project_name"]):
        raise PublishError("Invalid Cloudflare Worker name")
    if not os.access(value["intelligence_cli"], os.X_OK) or not Path(value["intelligence_cli"]).is_file():
        raise PublishError("Configured Intelligence CLI must be executable")
    if not Path(value["intelligence_data_dir"]).is_dir():
        raise PublishError("Configured Intelligence data directory must exist")
    return value


def atomic_state(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def publication_lock(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(directory / "publisher.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PublishError("Another publication is already running") from None
        yield
    finally:
        os.close(descriptor)


def run_command(command, *, cwd, env, timeout, stage):
    # Child output may contain report contents, local paths, or provider credentials.
    try:
        subprocess.run(command, cwd=cwd, env=env, timeout=timeout, check=True,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        raise PublishError(f"{stage} failed; successful cloud publication was not advanced") from None


def source_revision(root):
    try:
        status = subprocess.run(["git", "status", "--porcelain"], cwd=root, check=True,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=10)
        if status.stdout.strip():
            return None
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=10)
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def publish(config, *, deploy=False, root=ROOT):
    """Serialize runs and track built/deployed versions independently for retries."""
    local = root / ".local"
    with publication_lock(local):
        state_path = local / "publication-state.json"
        state = read_json(state_path) if state_path.exists() else {"built": None, "deployed": None}
        if not isinstance(state, dict) or set(state) != {"built", "deployed"}:
            raise PublishError("Invalid publisher state")
        env = dict(os.environ)
        env["ASTRO_TELEMETRY_DISABLED"] = "1"
        env["WRANGLER_SEND_METRICS"] = "false"
        run_command([sys.executable, "-m", "publisher.export", "--intelligence-cli", config["intelligence_cli"],
                     "--data-dir", config["intelligence_data_dir"], "--output", config["output_file"]],
                    cwd=root, env=env, timeout=180, stage="Collection")
        try:
            catalog = validate_publication(read_json(Path(config["output_file"])))
        except (PublicationError, TypeError, KeyError):
            raise PublishError("Public archive validation failed") from None
        revision = source_revision(root)
        version = {"catalog": catalog, "source_revision": revision}
        target = {"version": version, "project_name": config["project_name"], "platform": "workers-static-assets"}
        deployment_needed = deploy and (revision is None or state["deployed"] != target)
        # Other supported build commands can overwrite dist between runs. Every
        # pending upload therefore uses a fresh build from this validated archive.
        build_needed = deployment_needed or revision is None or state["built"] != version or not (root / "dist" / "index.html").is_file()
        if build_needed:
            # A failed build can partially replace dist; invalidate its previous marker first.
            state["built"] = None
            atomic_state(state_path, state)
            build_env = {key: value for key, value in env.items() if not key.startswith(("CLOUDFLARE_", "CF_API_"))}
            build_env["ZILNON_PUBLICATION_FILE"] = config["output_file"]
            npm = shutil.which("npm")
            if npm is None:
                raise PublishError("npm is unavailable on the publisher PATH")
            run_command([npm, "run", "build"], cwd=root, env=build_env, timeout=300, stage="Build")
            if not (root / "dist" / "index.html").is_file():
                raise PublishError("Build did not produce the static homepage")
            state["built"] = version
            atomic_state(state_path, state)
        if deployment_needed:
            run_command([str(root / "node_modules" / ".bin" / "wrangler"), "deploy", "--config", str(root / "wrangler.jsonc"),
                         "--name", config["project_name"]],
                        cwd=root, env=env, timeout=300, stage="Deployment")
            state["deployed"] = target
            atomic_state(state_path, state)
            return "Static publication deployed"
        return "Local publication built" if build_needed else "Public content and source revision unchanged"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--deploy", action="store_true", help="Deploy through authenticated Cloudflare Wrangler")
    args = parser.parse_args()
    try:
        print(publish(load_config(args.config), deploy=args.deploy))
    except PublishError as error:
        print(str(error), file=sys.stderr)
        return 1
    except OSError:
        # I/O exception values can disclose private paths.
        print("Publisher filesystem operation failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
