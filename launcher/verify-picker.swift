// Proves the launcher's native-picker wiring: registers a stub askwPick handler
// (returns a canned path instead of opening a panel), loads the launcher, confirms
// body.native is applied + the Browse button fills the input.
import Cocoa
@preconcurrency import WebKit

let CANNED = NSHomeDirectory() + "/Documents"

final class H: NSObject, WKNavigationDelegate, WKScriptMessageHandlerWithReply {
    var wv: WKWebView!
    func userContentController(_ u: WKUserContentController, didReceive m: WKScriptMessage,
                              replyHandler: @escaping (Any?, String?) -> Void) {
        replyHandler(CANNED, nil)   // stand in for NSOpenPanel
    }
    func webView(_ webView: WKWebView, didFinish nav: WKNavigation!) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
            let js = """
            var native = document.body.classList.contains('native');
            var btn = document.querySelector('.browse[data-pick="folder"]');
            var visible = btn && getComputedStyle(btn).display !== 'none';
            if(btn) btn.click();
            await new Promise(function(r){ setTimeout(r, 500); });
            return JSON.stringify({native:native, btnVisible:visible, folderValue:document.getElementById('folder').value});
            """
            self.wv.callAsyncJavaScript(js, arguments: [:], in: nil, in: .page) { r in
                switch r { case .success(let v): print("PICKER:", v)
                           case .failure(let e): print("ERR:", e) }
                exit(0)
            }
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let cfg = WKWebViewConfiguration()
let h = H()
cfg.userContentController.addScriptMessageHandler(h, contentWorld: .page, name: "askwPick")
let wv = WKWebView(frame: NSRect(x: 0, y: 0, width: 700, height: 700), configuration: cfg)
h.wv = wv; wv.navigationDelegate = h
wv.load(URLRequest(url: URL(string: "http://localhost:8899/")!))
DispatchQueue.main.asyncAfter(deadline: .now() + 20) { print("TIMEOUT"); exit(1) }
app.run()
