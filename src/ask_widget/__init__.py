"""ask-widget — highlight-to-ask reading companion.

A drop-in ``<script>`` widget plus a local FastAPI server that shells out to the
``claude`` CLI (read-only, with ``--add-dir <folder>``) so every answer has the
full context of a folder's ``CLAUDE.md`` and files.
"""

__version__ = "0.1.0"
