"""Interactive setup commands and helpers for Articulate (§3.3, §3.12)."""
import hashlib
import json
import logging
import subprocess
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

from articulate.core.launchd import (
    PMSET_COMMAND,
    get_repo_root,
    install_launch_agents,
)
from articulate.core.paths import (
    get_human_dir,
    models_toml_path,
    real_fixtures_dir,
)
from articulate.core.secrets import (
    SecretNotFoundError,
    get_canvas_token,
    get_gemini_key,
    get_google_oauth,
    get_pocket_key,
)

logger = logging.getLogger(__name__)


def store_secret(service: str, account: str | None, secret: str) -> bool:
    """
    Store a secret in macOS Keychain using `security add-generic-password -U` (§3.3).
    Never logs or prints the secret.
    """
    cmd = ["security", "add-generic-password", "-U", "-s", service]
    if account:
        cmd.extend(["-a", account])
    cmd.extend(["-w", secret])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return proc.returncode == 0
    except Exception as e:  # noqa: BLE001
        logger.error("Failed storing secret for service '%s': %s", service, e)
        return False


def setup_secrets(interactive: bool = True) -> dict[str, bool]:
    """
    Check and prompt for each required Keychain secret (§3.3).
    Gemini API key (`articulate-gemini`)
    Pocket API key (`articulate`, `pocket_api_key`)
    Canvas token (`articulate`, `canvas_token`)
    Google OAuth (`articulate`, `google_oauth`)
    """
    results: dict[str, bool] = {}

    # 1. Gemini API key
    try:
        get_gemini_key()
        results["gemini"] = True
    except SecretNotFoundError:
        if interactive:
            print("\n[Keychain] Missing Gemini API key (service: articulate-gemini)")
            val = input("Enter Gemini API key: ").strip()
            if val:
                results["gemini"] = store_secret("articulate-gemini", None, val)
            else:
                results["gemini"] = False
        else:
            results["gemini"] = False

    # 2. Pocket API key
    try:
        get_pocket_key()
        results["pocket"] = True
    except SecretNotFoundError:
        if interactive:
            print("\n[Keychain] Missing Pocket API key (service: articulate, account: pocket_api_key)")
            val = input("Enter Pocket API key (pk_...): ").strip()
            if val:
                results["pocket"] = store_secret("articulate", "pocket_api_key", val)
            else:
                results["pocket"] = False
        else:
            results["pocket"] = False

    # 3. Canvas token
    try:
        get_canvas_token()
        results["canvas"] = True
    except SecretNotFoundError:
        if interactive:
            print("\n[Keychain] Missing Canvas token (service: articulate, account: canvas_token)")
            val = input("Enter Canvas token: ").strip()
            if val:
                results["canvas"] = store_secret("articulate", "canvas_token", val)
            else:
                results["canvas"] = False
        else:
            results["canvas"] = False

    # 4. Google OAuth
    try:
        get_google_oauth()
        results["google"] = True
    except SecretNotFoundError:
        if interactive:
            print("\n[Keychain] Missing Google OAuth credentials (service: articulate, account: google_oauth)")
            # Look for client secret json in ~/Downloads
            downloads = Path.home() / "Downloads"
            candidates = list(downloads.glob("client_secret*.json")) + list(downloads.glob("*oauth*.json"))
            chosen_file: Path | None = None
            if candidates:
                chosen_file = candidates[0]
                print(f"Found Google client secret JSON: {chosen_file}")
            else:
                entered = input("Path to Google client secret JSON (in ~/Downloads): ").strip()
                if entered and Path(entered).expanduser().exists():
                    chosen_file = Path(entered).expanduser()

            if chosen_file:
                try:
                    from google_auth_oauthlib.flow import InstalledAppFlow

                    # Scopes for Google Calendar and YouTube (Phase 5)
                    scopes = [
                        "https://www.googleapis.com/auth/calendar.readonly",
                        "https://www.googleapis.com/auth/youtube.readonly",
                    ]
                    flow = InstalledAppFlow.from_client_secrets_file(str(chosen_file), scopes=scopes)
                    creds = flow.run_local_server(port=0)
                    oauth_data = {
                        "client_id": creds.client_id,
                        "client_secret": creds.client_secret,
                        "refresh_token": creds.refresh_token,
                        "token_uri": creds.token_uri,
                    }
                    results["google"] = store_secret("articulate", "google_oauth", json.dumps(oauth_data))
                except Exception as e:  # noqa: BLE001
                    logger.error("Google OAuth login flow failed: %s", e)
                    results["google"] = False
            else:
                results["google"] = False
        else:
            results["google"] = False

    return results


def setup_gemini_models(
    api_key: str | None = None,
    dest_path: Path | None = None,
) -> list[str]:
    """
    List Gemini models via REST ListModels, confirm available ids, and write models.toml (§3.2, §3.12, 0.11).
    """
    key = api_key
    if not key:
        try:
            key = get_gemini_key()
        except Exception as e:
            raise RuntimeError(f"Cannot setup Gemini models without Gemini API key: {e}") from e

    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
    req = urllib.request.Request(url, headers={"User-Agent": "articulate"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    available_models = [m.get("name", "").replace("models/", "") for m in data.get("models", [])]

    # Models we expect to use
    expected = [
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-4-hydrogen",
        "gemini-3.5-flash-lite",
        "gemini-embedding-2",
    ]
    confirmed = [m for m in expected if m in available_models]
    if not confirmed:
        # Fallback if names on free-tier differ or test mocks
        confirmed = [m for m in available_models if "gemini" in m][:5]

    # Render models.toml content
    toml_lines = [
        "# models.toml — confirmed model ids and quota settings (§3.2, Acceptance check 0.11)",
        "[gemini]",
        'keychain_service = "articulate-gemini"',
        "timeout_s = 600",
        "long_timeout_s = 1200",
        "",
    ]

    for model_name in confirmed:
        toml_lines.append(f'[gemini.models."{model_name}"]')
        if "flash-lite" in model_name:
            toml_lines.extend(["rpm = 15", "rpd = 500", "reserve = 40"])
        elif "embedding" in model_name:
            toml_lines.extend(["rpm = 100", "rpd = 1000", "reserve = 50"])
        else:
            toml_lines.extend(["rpm = 5", "rpd = 20"])
        toml_lines.append("")

    toml_lines.extend([
        "[claude]",
        'model = "sonnet"',
        "weekly_ceiling_pct = 50.0",
        "session_ceiling_pct = 70.0",
        "max_calls_per_night = 15",
        "usage_timeout_s = 30",
        "",
        "[ollama]",
        'url = "http://127.0.0.1:11434"',
        'text_model = "qwen3:8b"',
        'embed_model = "embeddinggemma"',
        'vl_model = "qwen3-vl:8b-instruct"',
        "",
        "[chains]",
        'extract = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-4-hydrogen", "claude@ramp"]',
        'guide_exam = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-4-hydrogen", "claude@ramp"]',
        'route_file = ["gemini-3.5-flash-lite"]',
        'embed_class = ["gemini-embedding-2"]',
        'rank_lecture = ["gemini-3.5-flash-lite"]',
        'library_judge = ["gemini-3.5-flash-lite"]',
        'definition_prompts = ["gemini-3.5-flash-lite"]',
        'method_prompts = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-4-hydrogen"]',
        'prompt_verify = ["gemini-3.5-flash-lite"]',
        'postclass_quiz = ["gemini-3.5-flash-lite"]',
        'lecture_literature = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]',
        'transcript_concepts = ["gemini-3.6-flash", "gemini-4-hydrogen", "gemini-3.8-flash"]',
        'contrast_writer = ["gemini-3.5-flash-lite"]',
        'youtube_long = ["gemini-3.6-flash"]',
        'youtube_short = ["gemini-3.5-flash-lite", "gemini-3.6-flash"]',
        'personal_literature = ["claude"]',
        'personal_text = ["ollama:qwen3:8b"]',
        'embed_personal = ["ollama:embeddinggemma"]',
        'vl_transcribe = ["ollama:qwen3-vl:8b-instruct"]',
    ])

    out_path = dest_path or models_toml_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(toml_lines) + "\n", encoding="utf-8")
    return confirmed


def setup_pocket_host(host_url: str = "https://public.heypocketai.com") -> bool:
    """Confirm Pocket REST host with one call (§3.12, Acceptance check 0.11)."""
    try:
        req = urllib.request.Request(
            host_url,
            headers={"User-Agent": "articulate"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            code = getattr(resp, "code", getattr(resp, "status", 200))
            return code < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception as e:  # noqa: BLE001
        logger.error("Pocket host check failed for %s: %s", host_url, e)
        return False


def setup_ollama_models(models: list[str] | None = None) -> bool:
    """Pull required Ollama models (§3.12)."""
    target_models = models or ["qwen3:8b", "embeddinggemma", "qwen3-vl:8b-instruct"]
    all_ok = True
    for m in target_models:
        print(f"Ensuring Ollama model: {m}...")
        cmd = ["ollama", "pull", m]
        res = subprocess.run(cmd, check=False)
        if res.returncode != 0:
            logger.warning("ollama pull %s exited with %d", m, res.returncode)
            all_ok = False
    return all_ok


def setup_launch_agents_step(
    repo_path: Path | None = None,
    uv_path: str | None = None,
    dest_dir: Path | None = None,
) -> list[Path]:
    """Install LaunchAgents and report paths (§3.12)."""
    installed = install_launch_agents(
        repo_path=repo_path,
        uv_path=uv_path,
        destination_dir=dest_dir,
    )
    return installed


def setup_keynote_consent() -> bool:
    """Run a Keynote AppleScript check to prompt for Automation permission (§3.12)."""
    script = 'tell application "Keynote" to get version'
    try:
        proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)
        return proc.returncode == 0
    except Exception as e:  # noqa: BLE001
        logger.warning("Keynote consent run failed: %s", e)
        return False


def check_icloud_drive_access() -> bool:
    """Check that iCloud Drive Articulate directory is accessible (§3.12)."""
    human_dir = get_human_dir()
    try:
        human_dir.mkdir(parents=True, exist_ok=True)
        test_file = human_dir / ".access_check"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("iCloud Drive access check failed on %s: %s", human_dir, e)
        return False


def copy_real_fixtures(
    manifest_path: Path | None = None,
    dest_dir: Path | None = None,
) -> tuple[list[str], list[str]]:
    """
    Read real-manifest.toml and copy any real files present on Amier's machine
    into DATA/fixtures/real/, verifying sha256 (§4, §3.12).
    Returns (copied_filenames, missing_filenames).
    """
    m_path = manifest_path or (get_repo_root() / "tests" / "fixtures" / "real-manifest.toml")
    if not m_path.exists():
        return [], []

    target_dir = dest_dir or real_fixtures_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    with open(m_path, "rb") as f:
        data = tomllib.load(f)

    copied = []
    missing = []

    for item in data.get("fixtures", []):
        fn = item.get("filename")
        expected_sha = item.get("sha256")
        src_path_str = item.get("source")
        if not fn or not expected_sha or not src_path_str:
            continue

        src = Path(src_path_str).expanduser()
        dest = target_dir / fn

        if src.exists():
            content = src.read_bytes()
            actual_sha = hashlib.sha256(content).hexdigest()
            if actual_sha == expected_sha:
                dest.write_bytes(content)
                copied.append(fn)
            else:
                logger.warning("sha256 mismatch for source %s, skipping copy", src)
                missing.append(fn)
        elif dest.exists():
            # Already in target_dir and matches
            content = dest.read_bytes()
            if hashlib.sha256(content).hexdigest() == expected_sha:
                copied.append(fn)
            else:
                missing.append(fn)
        else:
            missing.append(fn)

    return copied, missing


def run_setup(secrets_only: bool = False, interactive: bool = True) -> int:
    """
    Execute articulate setup (§3.12, 0.11, 0.17).
    """
    print("=== Articulate Setup ===")

    # 1. Secrets
    print("\n--- 1. Checking Keychain Secrets ---")
    secrets_res = setup_secrets(interactive=interactive)
    for k, ok in secrets_res.items():
        print(f"  {k}: {'ok' if ok else 'missing'}")

    if secrets_only:
        return 0

    # 2. Gemini ListModels -> models.toml
    print("\n--- 2. Gemini ListModels & models.toml ---")
    try:
        confirmed = setup_gemini_models()
        print(f"  Confirmed model ids: {', '.join(confirmed)}")
        print(f"  Wrote {models_toml_path()}")
    except Exception as e:  # noqa: BLE001
        print(f"  Gemini ListModels check error: {e}")

    # 3. Pocket host check
    print("\n--- 3. Pocket Host Reachability ---")
    pocket_ok = setup_pocket_host()
    print(f"  https://public.heypocketai.com: {'ok' if pocket_ok else 'unreachable'}")

    # 4. Ollama models
    print("\n--- 4. Ollama Models ---")
    try:
        setup_ollama_models()
    except Exception as e:  # noqa: BLE001
        print(f"  Ollama setup error: {e}")

    # 5. LaunchAgents
    print("\n--- 5. LaunchAgents Plists ---")
    installed = setup_launch_agents_step()
    for p in installed:
        print(f"  Installed: {p}")

    # 6. pmset wake line
    print("\n--- 6. Power Management (pmset) ---")
    print(f"  Run the following command once to wake your Mac for nightly runs:\n    {PMSET_COMMAND}")

    # 7. Keynote consent
    print("\n--- 7. Keynote Automation Consent ---")
    kn_ok = setup_keynote_consent()
    print(f"  Keynote osascript response: {'granted/ok' if kn_ok else 'prompted or not installed'}")

    # 8. iCloud Drive access check
    print("\n--- 8. iCloud Drive Access ---")
    ic_ok = check_icloud_drive_access()
    print(f"  iCloud Drive ({get_human_dir()}): {'accessible' if ic_ok else 'failed'}")

    # 9. Real fixtures collection
    print("\n--- 9. Real Fixtures Sync ---")
    copied, missing = copy_real_fixtures()
    print(f"  Copied {len(copied)} real fixtures to {real_fixtures_dir()}")
    if missing:
        print(f"  Missing {len(missing)} real fixtures (will be skipped during test runs)")

    print("\nSetup finished.")
    return 0
