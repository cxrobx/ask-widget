# Alfred integration

The **Open in Ask Widget** workflow adds a file-specific action to Alfred's
Universal Actions panel for HTML, Markdown, plain-text, and PDF documents.

Build Ask Widget first, then double-click:

```text
launcher/build/Open-in-Ask-Widget.alfredworkflow
```

After Alfred imports it, select a supported document, open Universal Actions
(right arrow inside Alfred by default), and choose **Open in Ask Widget**.

The workflow opens the selected file by Ask Widget's bundle identifier, so the
app must be installed but does not have to be running.
