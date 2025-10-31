"""Utility scripts for IPC Areas data processing."""

from .download_ipc_areas import IPCAreaDownloader
from .combine_ipc_areas import main as combine_main
from .simplify_ipc_global_areas import simplify_topojson, minify_topojson

__all__ = [
    "IPCAreaDownloader",
    "combine_main",
    "simplify_topojson",
    "minify_topojson",
]
