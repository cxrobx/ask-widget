# Security model

Onyx can invoke Claude or Codex against local files, so the local HTTP boundary is
deliberately narrow:

0. Browser origins are allowlisted, never `*`: `null` (file://), localhost, and
   `127.0.0.1`, plus anything listed in the `allowed_origins` setting. It holds
   `app://obsidian.md` by default so the Obsidian plugin can reach the API;
   clearing it shuts the plugin out at the server.
1. The service binds to `127.0.0.1` by default.
2. Host headers must be `127.0.0.1:<port>` or `localhost:<port>` to block DNS
   rebinding.
3. Browser origins are limited to local origins and `file://`'s `null` origin.
4. A random per-process token is embedded in `ask.js` and required by all
   mutations and provider actions.
5. Context folders are resolved through symlinks and must remain under a trusted
   root unless the explicit `--allow-any` escape hatch is used.
6. Claude is restricted to Read, Grep, and Glob on the folder, plus WebSearch,
   WebFetch, and skills so an answer can check an outside fact before stating
   it; Bash, Edit, and Write stay denied. No MCP servers load
   (`--strict-mcp-config`), so MCP tools your own Claude settings pre-allow
   never reach an answer. Codex runs headlessly with a read-only sandbox, live
   web search, no approvals, no user rules, and optional tool/plugin features
   disabled. Web access has one cost: a document's own text could try to steer
   a fetch, and whatever goes into its URL, to an outside site. Settings →
   Answers → *Check outside facts on the web* turns it off for both providers,
   Codex's cached search included, for faster answers from the folder and
   skills only.
7. Remote fetches reject credentials, non-HTML content, responses over 12 MB,
   and private, loopback, link-local, reserved, or multicast addresses unless
   private URLs are explicitly enabled.
8. Local document assets use unguessable capabilities that expire after eight
   hours and are bounded to the 32 most recently opened documents. JSON and
   source-map assets are never served.
9. Remote HTML scripts and inline handlers are removed. Trusted local HTML may
   run its authored scripts, but script connections stay restricted to the local
   service; plugins and base-URL changes remain blocked.
10. File citations are displayed only after canonicalizing them and proving they
    exist inside the active context folder.
11. Artifacts writes only symlinks and folders, and only inside its own
    folders: never through a linked folder (that would write into the tree it
    points at), never over an existing entry, never a link to something that
    contains the Artifacts folder. Moving, renaming, pinning and removing act
    only on an entry sitting directly in one of its own folders; removing
    deletes a link or an empty folder, never a real file. Link targets are never
    modified.
12. Editing a note (⌘E) writes only the file the page was opened from. The
    source is handed out against that page's own document capability, with an
    edit capability for that one file; a save names no path. It writes only an
    existing UTF-8 Markdown file, in place, never through a path that has
    become a symlink, and never over a version newer than the one the editor
    last read (the save is refused and the editor asks). This is the reader's
    own write path: the provider runners still have no write tools.

Both executables are launched directly, not through an interactive shell.
Before launch, Anthropic/OpenAI API-key and alternate-provider environment
variables are removed so the verified subscription session is used.
