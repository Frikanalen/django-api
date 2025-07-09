from pathlib import Path
import logging
import environ


def find_project_root() -> Path:
    marker = "manage.py"
    current = Path(__file__).resolve()
    while current != current.parent:
        if (current / marker).exists():
            return current
        current = current.parent
    raise RuntimeError(f"Project root with {marker} not found")


def load_env_from(file: str) -> None:
    """Load environment variables from a specified file."""
    environ.Env.read_env(str(find_project_root() / file))
    logging.info(f"Loading environment from {file}")


env = environ.Env(
    ALLOWED_HOSTS=(str, "frikanalen.no,forrige.frikanalen.no,beta.frikanalen.no"),
    SMTP_SERVER=(str,),
    SECRET_KEY=(str,),
)
