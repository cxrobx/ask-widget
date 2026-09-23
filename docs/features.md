# Features

The full feature list, moved from the README.

## What it includes

- A native **Onyx.app** with its own frozen Python service. The installed
  app does not depend on this checkout, a project virtual environment, or a
  system Python.
- The same macOS glass as cxtasks and cxmail: a plain Gaussian blur of the
  desktop behind the window (`CGSSetWindowBackgroundBlurRadius`, resolved at
  runtime, with `NSVisualEffectView` as the fallback), alpha-aware
  sidebar/content/card tints over it, persistent System/Light/Dark themes, and
  one transparency slider that drives both the tints and the blur radius
  (10–48, 24 at the default). The sidebar never gets thinner than the opacity
  that keeps its labels at WCAG AA contrast over any backdrop (79% dark, 84%
  light), so a bright window behind it can't wash the list out. The window
  opens opaque and turns to glass after the first paint; macOS **Reduce
  Transparency** is honoured live.
- **Library**, where the app opens: both vaults' trees in one sidebar, and a
  home page of recently opened documents (notes, artifacts, anything else) and
  recent asks. Per-document reading position and Markdown note export.
- HTML, Markdown, plain-text, and text-based PDF readers. Trusted local HTML keeps
  its buttons and scripts; PDF selections retain their page number for prompts
  and evidence.
- Streaming answers with first-activity and total timeouts, heartbeats, Stop,
  Retry, follow-up questions, and visible traces of each file read, search, and web lookup.
- A native provider selector and CLI-discovered model selectors. Claude models
  come from the aliases advertised by the installed Claude CLI; Codex models
  and supported reasoning efforts come from its live account model catalog.
- Subscription-only execution. Claude must be signed in through claude.ai and
  Codex through ChatGPT. API-key environment variables are removed and
  non-subscription sessions are rejected to avoid metered API charges.
- Validated evidence cards. File references are resolved inside the active
  context folder, include nearby source lines, and can open in VS Code, Cursor,
  or the default app.
- A bounded, expiring local answer cache keyed by document version, model,
  context folder, selection, and page.
- Persistent SQLite history in WAL mode with schema versioning.
- **Search** (⌘P, or File ▸ Search…), a box over the window as in Obsidian.
  Titles match as you type; passages inside notes and artifacts follow, ranked
  by their words and their meaning together, each with its section and a
  snippet, and ↩ opens one in the reader at that section. Passages come from
  the index [vault-mcp](https://github.com/cxrobx/vault-mcp) keeps, read in
  place (`ONYX_VAULT_INDEX` names another), and meaning from its embedding
  model in a local Ollama. Without Ollama it searches words; without the
  index, titles.
- **Find in page** (⌘F, or Edit ▸ Find ▸ Find…), a bar at the reader's
  top-right that finds words in the note, artifact, or PDF being read, as in
  Safari: every match tinted and the current one stronger, ↩ or ⌘G for the
  next, ⇧↩ or ⇧⌘G for the previous, and a match folded inside a shut section
  opened on the way. Case and accents don't matter. Escape leaves the match
  selected, so a right-click can ask about it.
- **Recent conversations**, a searchable dialog (the clock at the sidebar's
  foot, or ⌘Y) with question-type, provider, model, document, and date filters.
  Saved entries can be asked again with the current model, edited before asking,
  or restored as a visible conversation, each in the reader beside the sidebar.
- **Chats on this page**, a chat bubble in the reader's bottom-right corner on
  any page with saved answers, with how many there are. It lists every
  conversation about the page, newest first; pick one to continue it in the
  answer panel. Its **Show highlights** toggle reveals subtle marks on passages
  discussed in those chats; marks stay off until you turn it on. Select text and
  choose **Save highlight** to keep a passage without asking a model. Saved
  highlights and their optional notes live in the same popover.
- **Settings**, a dialog behind the cog at the sidebar's foot (or ⌘,), as in
  cxtasks: window glass, provider, model, reasoning effort, response detail,
  caching, history, timeouts, private URL access, and trusted context roots.
  Each setting applies as it changes; there are no Save buttons.
- Diagnostics for both CLI installations, both subscription sessions, the
  selected model, database, default folder, and an optional live provider probe.
- Native **File ▸ Open Document…**, file-association support, and a macOS
  **Services ▸ Ask Selection with Onyx** action.
- Browser-style **Command-Plus**, **Command-Minus**, and **Command-0** zoom,
  also available from the View menu.
- **Open in Claude/Codex** for continuing with the selected provider in a dedicated terminal session. Hold
  Option to copy the prepared handoff prompt instead.

## Recent versions

Version 0.5 adds a reusable local question-history workspace: inspect full saved
answers, filter by passage, document, provider, model, or date, ask a saved
question again, edit it before asking, or restore its answer and continue the
visible conversation. Version 0.4 introduced subscription-backed Claude and
Codex providers, live model catalogs, and native model selectors.
