import logging
import os
import shutil

logger = logging.getLogger(__name__)


def delete_files(paths: list[str]) -> None:
    for path in paths:
        try:
            if os.path.exists(path):
                os.remove(path)
                logger.debug("Deleted temp file: %s", path)
        except OSError as e:
            logger.warning("Could not delete %s: %s", path, e)


def delete_dir(path: str) -> None:
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
            logger.debug("Deleted temp dir: %s", path)
    except OSError as e:
        logger.warning("Could not delete dir %s: %s", path, e)
