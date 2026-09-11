// Draws the Onyx app icon (1024×1024 PNG): the gem in onyx-gem.png alone on
// a transparent canvas — no tile — sized like Chris's custom Obsidian icon,
// whose stone is 56% of the canvas height, centred.
//   make-icon <out.png> <gem.png>
import AppKit

let args = CommandLine.arguments
guard args.count == 3,
      let gem = NSImage(contentsOfFile: args[2])?
          .cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("usage: make-icon <out.png> <gem.png>\n".data(using: .utf8)!)
    exit(2)
}

let rgb = CGColorSpace(name: CGColorSpace.sRGB)!
let rgba = CGImageAlphaInfo.premultipliedLast.rawValue

/// The stone's own bounds in bottom-left coordinates: pixels at least this
/// opaque, so a soft halo in the art is carried along but not measured.
func bodyBounds(of image: CGImage, alphaFloor: UInt8 = 40) -> CGRect {
    let w = image.width, h = image.height
    var pixels = [UInt8](repeating: 0, count: w * h * 4)
    let scan = CGContext(
        data: &pixels, width: w, height: h, bitsPerComponent: 8,
        bytesPerRow: w * 4, space: rgb, bitmapInfo: rgba
    )!
    scan.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
    var minX = w, maxX = -1, minRow = h, maxRow = -1
    for row in 0..<h {
        for x in 0..<w where pixels[(row * w + x) * 4 + 3] >= alphaFloor {
            minX = min(minX, x); maxX = max(maxX, x)
            minRow = min(minRow, row); maxRow = max(maxRow, row)
        }
    }
    guard maxX >= 0 else { return CGRect(x: 0, y: 0, width: w, height: h) }
    // Memory row 0 is the top of the image; drawing space starts at the bottom.
    return CGRect(x: minX, y: h - 1 - maxRow, width: maxX - minX + 1, height: maxRow - minRow + 1)
}

let S: CGFloat = 1024
let ctx = CGContext(
    data: nil, width: Int(S), height: Int(S), bitsPerComponent: 8,
    bytesPerRow: 0, space: rgb, bitmapInfo: rgba
)!

let body = bodyBounds(of: gem)
let scale = S * 0.56 / body.height
let centre = CGPoint(x: S / 2, y: S / 2)
let origin = CGPoint(x: centre.x - body.midX * scale, y: centre.y - body.midY * scale)
ctx.interpolationQuality = .high
ctx.draw(gem, in: CGRect(x: origin.x, y: origin.y,
                         width: CGFloat(gem.width) * scale, height: CGFloat(gem.height) * scale))

guard let image = ctx.makeImage(),
      let png = NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]) else {
    FileHandle.standardError.write("icon render failed\n".data(using: .utf8)!)
    exit(1)
}
try! png.write(to: URL(fileURLWithPath: args[1]))
