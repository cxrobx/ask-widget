// Ask Widget — macOS launcher.
// Starts the local FastAPI server (if it isn't already up) and shows the launcher
// in a WKWebView window. Pattern adapted from resume-platform/launcher-swift.
import Cocoa
@preconcurrency import WebKit
import UniformTypeIdentifiers

let PORT = 8899
let BASE = "http://localhost:\(PORT)"

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandlerWithReply {

    private var window: NSWindow!
    private var webView: WKWebView?
    private var server: Process?
    private var didSpawn = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        buildLoadingWindow()
        // If a server is already healthy (e.g. started from a terminal), reuse it;
        // otherwise spawn our own. Either way, open the UI once it answers /health.
        checkHealth { [weak self] alive in
            if alive {
                DispatchQueue.main.async { self?.openApp() }
            } else {
                self?.startServer()
                self?.waitForServer(attempts: 40) {
                    DispatchQueue.main.async { self?.openApp() }
                }
            }
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        if didSpawn { server?.terminate() }   // only kill a server WE started
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        if !hasVisibleWindows { window?.makeKeyAndOrderFront(nil) }
        return true
    }

    // MARK: - Server

    private func startServer() {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let repo = "\(home)/Projects/ask-widget"
        // `zsh -l` so the spawned python inherits the login PATH and can find the
        // `claude` binary (~/.local/bin). `exec` so terminate() hits python, not zsh.
        // Extra --allow-root entries come from ~/.config/ask-widget/allow-roots
        // (one absolute path per line, ~ allowed, # comments). Optional.
        var extraRoots = ""
        let rootsFile = "\(home)/.config/ask-widget/allow-roots"
        if let raw = try? String(contentsOfFile: rootsFile, encoding: .utf8) {
            for line in raw.split(separator: "\n") {
                let t = line.trimmingCharacters(in: .whitespaces)
                guard !t.isEmpty, !t.hasPrefix("#") else { continue }
                let path = t.hasPrefix("~")
                    ? home + t.dropFirst()
                    : t
                extraRoots += " --allow-root \(path)"
            }
        }
        let cmd = "exec .venv/bin/python -m ask_widget"
            + " --folder \(home)/Projects"
            + extraRoots
            + " --port \(PORT)"
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/zsh")
        p.arguments = ["-l", "-c", cmd]
        p.currentDirectoryURL = URL(fileURLWithPath: repo)
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        do {
            try p.run()
            server = p
            didSpawn = true
        } catch {
            showError("Couldn't start the server.\n\nMake sure ~/Projects/ask-widget/.venv exists (python3 -m venv .venv && pip install -e .).")
        }
    }

    private func checkHealth(_ done: @escaping (Bool) -> Void) {
        var req = URLRequest(url: URL(string: "\(BASE)/health")!)
        req.timeoutInterval = 1.5
        URLSession.shared.dataTask(with: req) { _, resp, _ in
            done((resp as? HTTPURLResponse)?.statusCode == 200)
        }.resume()
    }

    private func waitForServer(attempts: Int, completion: @escaping () -> Void) {
        guard attempts > 0 else {
            DispatchQueue.main.async {
                self.showError("The server did not start in time.\n\nTry running it from a terminal:\n  cd ~/Projects/ask-widget && ./run.sh")
            }
            return
        }
        checkHealth { [weak self] alive in
            if alive { completion() }
            else { DispatchQueue.global().asyncAfter(deadline: .now() + 0.75) { self?.waitForServer(attempts: attempts - 1, completion: completion) } }
        }
    }

    // MARK: - UI

    private func openApp() {
        let cfg = WKWebViewConfiguration()
        cfg.defaultWebpagePreferences.allowsContentJavaScript = true
        // Native file/folder picker bridge: the launcher page calls
        // window.webkit.messageHandlers.askwPick.postMessage({kind}) and gets back
        // the chosen absolute path (impossible in a plain browser).
        cfg.userContentController.addScriptMessageHandler(self, contentWorld: .page, name: "askwPick")
        let wv = WKWebView(frame: .zero, configuration: cfg)
        wv.navigationDelegate = self
        wv.uiDelegate = self
        wv.allowsBackForwardNavigationGestures = true
        wv.load(URLRequest(url: URL(string: "\(BASE)/")!))
        webView = wv

        window.styleMask = [.titled, .closable, .miniaturizable, .resizable]
        window.titlebarAppearsTransparent = false
        window.title = "Ask Widget"
        window.contentView = wv
        window.setContentSize(NSSize(width: 1200, height: 860))
        window.minSize = NSSize(width: 720, height: 520)
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func buildLoadingWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 360, height: 170),
                          styleMask: [.titled, .closable, .fullSizeContentView],
                          backing: .buffered, defer: false)
        window.titlebarAppearsTransparent = true
        window.title = "Ask Widget"
        window.isMovableByWindowBackground = true
        window.center()

        let root = NSView()
        let spinner = NSProgressIndicator()
        spinner.style = .spinning; spinner.startAnimation(nil)
        spinner.translatesAutoresizingMaskIntoConstraints = false
        let title = NSTextField(labelWithString: "Ask Widget")
        title.font = .boldSystemFont(ofSize: 15); title.translatesAutoresizingMaskIntoConstraints = false
        let sub = NSTextField(labelWithString: "Starting…")
        sub.font = .systemFont(ofSize: 13); sub.textColor = .secondaryLabelColor
        sub.translatesAutoresizingMaskIntoConstraints = false
        [spinner, title, sub].forEach { root.addSubview($0) }
        window.contentView = root
        NSLayoutConstraint.activate([
            spinner.centerXAnchor.constraint(equalTo: root.centerXAnchor),
            spinner.centerYAnchor.constraint(equalTo: root.centerYAnchor, constant: -16),
            title.centerXAnchor.constraint(equalTo: root.centerXAnchor),
            title.topAnchor.constraint(equalTo: spinner.bottomAnchor, constant: 14),
            sub.centerXAnchor.constraint(equalTo: root.centerXAnchor),
            sub.topAnchor.constraint(equalTo: title.bottomAnchor, constant: 4),
        ])
        window.makeKeyAndOrderFront(nil)
    }

    // Native picker bridge → returns the chosen absolute path (or null if cancelled).
    func userContentController(_ ucc: WKUserContentController, didReceive message: WKScriptMessage,
                              replyHandler: @escaping (Any?, String?) -> Void) {
        let body = message.body as? [String: Any] ?? [:]
        let kind = body["kind"] as? String ?? "folder"
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = false
        if kind == "file" {
            panel.canChooseFiles = true
            panel.canChooseDirectories = false
            panel.allowedContentTypes = [UTType.html]
            panel.prompt = "Open"
            panel.message = "Choose an HTML document to open with the widget"
        } else {
            panel.canChooseFiles = false
            panel.canChooseDirectories = true
            panel.canCreateDirectories = false
            panel.prompt = "Use Folder"
            panel.message = "Choose the context folder Claude should read (its CLAUDE.md + files)"
        }
        if let initial = body["initial"] as? String, !initial.isEmpty,
           FileManager.default.fileExists(atPath: initial) {
            panel.directoryURL = URL(fileURLWithPath: initial)
        }
        panel.begin { resp in
            replyHandler(resp == .OK ? panel.url?.path : nil, nil)
        }
    }

    // Let target=_blank / window.open links open in the same view.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url { webView.load(URLRequest(url: url)) }
        return nil
    }

    // MARK: - Menu

    @objc private func reload() { webView?.reload() }
    @objc private func goHome() { webView?.load(URLRequest(url: URL(string: "\(BASE)/")!)) }
    @objc private func openInBrowser() {
        let url = webView?.url ?? URL(string: "\(BASE)/")!
        NSWorkspace.shared.open(url)
    }
    @objc private func openDocument() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [UTType.html]
        panel.allowsMultipleSelection = false
        panel.begin { [weak self] resp in
            guard resp == .OK, let path = panel.url?.path else { return }
            var comps = URLComponents(string: "\(BASE)/view")!
            comps.queryItems = [URLQueryItem(name: "src", value: path)]
            if let u = comps.url { self?.webView?.load(URLRequest(url: u)) }
        }
    }

    private func menuItem(_ title: String, _ action: Selector, _ key: String) -> NSMenuItem {
        let mi = NSMenuItem(title: title, action: action, keyEquivalent: key)
        mi.target = self
        return mi
    }

    private func buildMenu() {
        let main = NSMenu()

        let appItem = NSMenuItem(); main.addItem(appItem)
        let appMenu = NSMenu(); appItem.submenu = appMenu
        appMenu.addItem(NSMenuItem(title: "About Ask Widget", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: ""))
        appMenu.addItem(.separator())
        appMenu.addItem(NSMenuItem(title: "Hide Ask Widget", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h"))
        appMenu.addItem(NSMenuItem(title: "Quit Ask Widget", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))

        let fileItem = NSMenuItem(); main.addItem(fileItem)
        let fileMenu = NSMenu(title: "File"); fileItem.submenu = fileMenu
        fileMenu.addItem(menuItem("Open Document…", #selector(openDocument), "o"))
        fileMenu.addItem(menuItem("Launcher Home", #selector(goHome), "n"))

        // Edit menu — required for Cmd+C/V/X/A inside WKWebView (Ask textarea, paste).
        let editItem = NSMenuItem(); main.addItem(editItem)
        let editMenu = NSMenu(title: "Edit"); editItem.submenu = editMenu
        editMenu.addItem(NSMenuItem(title: "Undo", action: Selector(("undo:")), keyEquivalent: "z"))
        editMenu.addItem(NSMenuItem(title: "Redo", action: Selector(("redo:")), keyEquivalent: "Z"))
        editMenu.addItem(.separator())
        editMenu.addItem(NSMenuItem(title: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x"))
        editMenu.addItem(NSMenuItem(title: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c"))
        editMenu.addItem(NSMenuItem(title: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v"))
        editMenu.addItem(NSMenuItem(title: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a"))

        let viewItem = NSMenuItem(); main.addItem(viewItem)
        let viewMenu = NSMenu(title: "View"); viewItem.submenu = viewMenu
        viewMenu.addItem(menuItem("Reload", #selector(reload), "r"))
        viewMenu.addItem(menuItem("Open in Default Browser", #selector(openInBrowser), "B"))

        let winItem = NSMenuItem(); main.addItem(winItem)
        let winMenu = NSMenu(title: "Window"); winItem.submenu = winMenu
        winMenu.addItem(NSMenuItem(title: "Minimize", action: #selector(NSWindow.miniaturize(_:)), keyEquivalent: "m"))
        winMenu.addItem(NSMenuItem(title: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w"))

        NSApp.mainMenu = main
    }

    private func showError(_ message: String) {
        DispatchQueue.main.async {
            let a = NSAlert(); a.alertStyle = .critical; a.messageText = "Ask Widget"
            a.informativeText = message; a.addButton(withTitle: "Quit")
            a.runModal(); NSApp.terminate(nil)
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
