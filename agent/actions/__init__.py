from .apps import open_app
from .files import find_file, list_dir, open_path
from .downloads import register_download, get_file_content

ACTIONS = {
    "open_app": open_app,
    "find_file": find_file,
    "list_dir": list_dir,
    "open_path": open_path,
    "download_file": register_download,
    "get_file_content": get_file_content,
}
