// One-off: prove the widget works in WebKit (the engine WKWebView/the app uses).
// Loads a /view doc, drives select → contextmenu → ELI5 via JS, and prints whether
// the answer streamed. Run: swiftc -framework Cocoa -framework WebKit verify-webkit.swift -o /tmp/vw && /tmp/vw "<view-url>"
import Cocoa
import WebKit

let url = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "http://localhost:8899/"

final class D: NSObject, WKNavigationDelegate {
    var wv: WKWebView!
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        // give assets + the widget a moment, then drive the flow
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { self.drive() }
    }
    func drive() {
        let js = """
        (function(){
          if(!window.__askWidget) return 'NO_WIDGET';
          var p=[...document.querySelectorAll('p')].find(e=>e.textContent.trim().length>100);
          if(!p) return 'NO_PARA';
          var r=document.createRange(); r.selectNodeContents(p);
          var s=getSelection(); s.removeAllRanges(); s.addRange(r);
          var rc=p.getBoundingClientRect();
          p.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:rc.left+20,clientY:rc.top+10}));
          var menu=document.querySelector('.askw-menu');
          var shown=menu&&menu.style.display==='block';
          var eli5=[...document.querySelectorAll('.askw-item')].find(i=>i.getAttribute('data-act')==='eli5');
          if(eli5) eli5.click();
          return 'MENU='+shown+' PILL='+(document.querySelector('.askw-pill-label')||{}).textContent;
        })();
        """
        wv.evaluateJavaScript(js) { res, err in
            print("drive:", res ?? "", err.map{"err=\($0)"} ?? "")
            DispatchQueue.main.asyncAfter(deadline: .now() + 22) { self.readResult() }
        }
    }
    func readResult() {
        let js = """
        (function(){
          var b=document.querySelector('.askw-body'); if(!b) return 'NO_PANEL';
          var t=b.textContent||'';
          return JSON.stringify({len:t.length, streamed:(t.length>40 && !b.querySelector('.askw-err') && !b.querySelector('.askw-think')), preview:t.slice(0,90), marked:typeof window.marked, dompurify:typeof window.DOMPurify});
        })();
        """
        wv.evaluateJavaScript(js) { res, _ in
            print("RESULT:", res ?? "nil")
            exit(0)
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let cfg = WKWebViewConfiguration()
let wv = WKWebView(frame: NSRect(x: 0, y: 0, width: 1200, height: 900), configuration: cfg)
let d = D(); d.wv = wv; wv.navigationDelegate = d
wv.load(URLRequest(url: URL(string: url)!))
DispatchQueue.main.asyncAfter(deadline: .now() + 45) { print("TIMEOUT"); exit(1) }
app.run()
