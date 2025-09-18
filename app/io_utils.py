# app/io_utils.py
from pathlib import Path
import shutil
from datetime import datetime

DATA_ACTIVE = Path("data/active")
DATA_ARCHIVE = Path("data/archive")

def safe_replace_upload(src_path: Path, dest_name: str = "sales.xlsx") -> Path:
    """
    Copy the uploaded file into data/active as `dest_name`.
    If an active file already exists, move it into data/archive with a timestamped name.
    Returns the path to the new active file.
    """
    DATA_ACTIVE.mkdir(parents=True, exist_ok=True)
    DATA_ARCHIVE.mkdir(parents=True, exist_ok=True)

    dest = DATA_ACTIVE / dest_name
    if dest.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        archived = DATA_ARCHIVE / f"{dest.stem}_{ts}{dest.suffix}"
        shutil.move(dest, archived)

    shutil.copy2(src_path, dest)
    return dest

