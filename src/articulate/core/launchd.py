"""launchd jobs and plist templates for Articulate (§3.12)."""
import shutil
from pathlib import Path

from articulate.core.paths import get_data_dir

PMSET_COMMAND = "sudo pmset repeat wake MTWRFSU 01:55:00"

JOB_PLIST_MAP = {
    "nightly": "com.amierlorenz.articulate.nightly.plist",
    "pass2": "com.amierlorenz.articulate.pass2.plist",
    "watch": "com.amierlorenz.articulate.watch.plist",
}


def get_repo_root() -> Path:
    """Find the repository root directory."""
    # Try traversing upwards from this file
    current = Path(__file__).resolve().parent
    for p in [current, *current.parents]:
        if (p / ".git").exists() or (p / "pyproject.toml").exists():
            return p
    return current


def get_launchd_templates_dir() -> Path:
    """Path to the launchd templates directory."""
    repo = get_repo_root()
    d = repo / "launchd"
    if d.exists():
        return d
    return Path(__file__).resolve().parent.parent.parent.parent / "launchd"


def get_default_uv_path() -> str:
    """Find uv binary on PATH or common brew locations."""
    which_uv = shutil.which("uv")
    if which_uv:
        return which_uv
    for candidate in [
        "/opt/homebrew/bin/uv",
        "/usr/local/bin/uv",
        Path.home() / ".cargo/bin/uv",
    ]:
        p = Path(candidate)
        if p.exists():
            return str(p)
    return "uv"


def render_plist(
    job: str,
    repo_path: Path | None = None,
    uv_path: str | None = None,
    log_dir: Path | None = None,
) -> str:
    """
    Render a launchd plist template for the given job (§3.12).
    Substitutes {{UV_PATH}}, {{REPO_PATH}}, and {{LOG_DIR}}.
    """
    if job not in JOB_PLIST_MAP:
        raise ValueError(f"Unknown job '{job}', expected one of {list(JOB_PLIST_MAP.keys())}")

    filename = JOB_PLIST_MAP[job]
    template_path = get_launchd_templates_dir() / filename
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")

    r_path = (repo_path or get_repo_root()).resolve()
    u_path = uv_path or get_default_uv_path()
    l_dir = (log_dir or (get_data_dir() / "logs"))

    template = template_path.read_text(encoding="utf-8")
    rendered = (
        template.replace("{{UV_PATH}}", u_path)
        .replace("{{REPO_PATH}}", str(r_path))
        .replace("{{LOG_DIR}}", str(l_dir))
    )
    return rendered


def render_all_plists(
    repo_path: Path | None = None,
    uv_path: str | None = None,
    log_dir: Path | None = None,
) -> dict[str, str]:
    """Render all 3 launchd plists."""
    return {
        JOB_PLIST_MAP[job]: render_plist(job, repo_path=repo_path, uv_path=uv_path, log_dir=log_dir)
        for job in ["nightly", "pass2", "watch"]
    }


def get_launch_agents_dir() -> Path:
    """Path to ~/Library/LaunchAgents/"""
    return Path("~/Library/LaunchAgents").expanduser().resolve()


def install_launch_agents(
    repo_path: Path | None = None,
    uv_path: str | None = None,
    destination_dir: Path | None = None,
) -> list[Path]:
    """
    Render and install the three LaunchAgents plists (§3.12).
    Returns the list of installed paths.
    """
    dest = destination_dir or get_launch_agents_dir()
    dest.mkdir(parents=True, exist_ok=True)

    installed = []
    plists = render_all_plists(repo_path=repo_path, uv_path=uv_path)
    for filename, content in plists.items():
        target = dest / filename
        target.write_text(content, encoding="utf-8")
        installed.append(target)

    return installed
