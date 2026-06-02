// Draws the Ask Widget app icon (1024×1024 PNG) with AppKit — no external asset.
// Motif: a warm "paper" card with a highlighted line of text + a cursor, on the
// burnt-orange accent, matching the widget's look.
import AppKit

let S: CGFloat = 1024
let img = NSImage(size: NSSize(width: S, height: S))
img.lockFocus()
let ctx = NSGraphicsContext.current!.cgContext

func rrect(_ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat, _ r: CGFloat) -> NSBezierPath {
    NSBezierPath(roundedRect: NSRect(x: x, y: y, width: w, height: h), xRadius: r, yRadius: r)
}

// Background squircle (leave a margin so macOS's own mask doesn't clip it)
let orange = NSColor(red: 0.76, green: 0.25, blue: 0.05, alpha: 1)      // #C2410C
let orangeDark = NSColor(red: 0.60, green: 0.20, blue: 0.04, alpha: 1)  // #9A3412
let grad = NSGradient(starting: orange, ending: orangeDark)!
let bg = rrect(96, 96, S - 192, S - 192, 196)
grad.draw(in: bg, angle: -90)

// Paper card
let cream = NSColor(red: 0.996, green: 0.988, blue: 0.980, alpha: 1)
NSColor.black.withAlphaComponent(0.18).setFill()
rrect(250, 232, 540, 540, 64).fill()                 // soft shadow
cream.setFill()
rrect(240, 248, 540, 540, 64).fill()                 // the card

// Text lines on the card
let ink = NSColor(red: 0.11, green: 0.10, blue: 0.09, alpha: 1)
let faint = NSColor(red: 0.80, green: 0.78, blue: 0.76, alpha: 1)
faint.setFill()
rrect(312, 648, 360, 26, 13).fill()                  // line 1
rrect(312, 588, 300, 26, 13).fill()                  // line 2

// The highlighted line (orange marker) + dark text over it + a cursor
NSColor(red: 0.98, green: 0.62, blue: 0.30, alpha: 0.55).setFill()
rrect(300, 446, 392, 64, 18).fill()                  // highlighter swipe
ink.setFill()
rrect(312, 466, 332, 26, 13).fill()                  // text on the highlight
orange.setFill()
rrect(660, 440, 18, 76, 6).fill()                    // caret

faint.setFill()
rrect(312, 360, 250, 26, 13).fill()                  // line 4

// Small "ask" speech dot cluster bottom-right of the card
ink.withAlphaComponent(0.85).setFill()
for i in 0..<3 {
    NSBezierPath(ovalIn: NSRect(x: 600 + CGFloat(i) * 44, y: 312, width: 26, height: 26)).fill()
}

img.unlockFocus()

guard let tiff = img.tiffRepresentation,
      let rep = NSBitmapImageRep(data: tiff),
      let png = rep.representation(using: .png, properties: [:]) else {
    FileHandle.standardError.write("icon render failed\n".data(using: .utf8)!)
    exit(1)
}
let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon.png"
try! png.write(to: URL(fileURLWithPath: out))
