# Alfred integration

The **Open in Onyx** workflow adds a file-specific action to Alfred's
Universal Actions panel for HTML, Markdown, plain-text, and PDF documents.

Build Onyx first, then double-click:

```text
launcher/build/Open-in-Onyx.alfredworkflow
```

After Alfred imports it, select a supported document, open Universal Actions
(right arrow inside Alfred by default), and choose **Open in Onyx**.

The workflow opens the selected file by Onyx's bundle identifier, so the
app must be installed but does not have to be running.
