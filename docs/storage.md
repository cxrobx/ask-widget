# Library, storage, and settings

**Library** is where the app opens. Its sidebar holds both vaults' trees, and
until you open something the reader's place holds its home page: an **Open**
field, **Recently opened** (each tagged Note, Artifact, or by kind, with where it
lives), and **Recent asks**, each of which opens its conversation. Choosing
Library again from Library returns to the home page. **Settings** (the cog at the
sidebar's foot, or ⌘,) and **Recent conversations** (the clock beside it, or ⌘Y)
are dialogs; the old launcher links `/#settings`, `/#diagnostics`, and
`/#history` open them.

The app stores its database at:

```text
~/Library/Application Support/Onyx/onyx.db
```

On its first start, Onyx copies the database it kept as Ask Widget
(`~/Library/Application Support/Ask Widget/ask-widget.db`) into place with
SQLite's backup API and leaves the original untouched.

Saved data includes settings, trusted roots, recent documents, reading
positions, requests, answers, citations, tool traces, errors, timing, and links
between original questions, reruns, edits, and continuations. Open **Recent
conversations** to search or reuse them. Turn off **Save reading history** in Settings to stop
persisting new conversations.
Browser/WKWebView answer-cache entries live in local storage and obey the cache
TTL and maximum-entry settings.

Saving a **Vault folder** in Settings also registers it as an allowed context
root, which is what lets a question asked inside a note cite that note's
neighbours. Clearing the field hides Vault mode and leaves the root in place.
The **Artifacts folder** is not registered itself — it holds only links — but
each folder linked into it is (see [Artifacts](reader.md#artifacts)).

Additional trusted roots can be managed in Settings. The launcher also reads
`~/.config/onyx/allow-roots` at startup for compatibility; use one path per
line, with `~` expansion and `#` comments supported.
