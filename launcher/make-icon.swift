// Draws the Onyx app icon (1024×1024 PNG): the gem in onyx-gem.png set on a
// near-black macOS tile, with a faint red bloom behind it so the stone still
// reads at 16 px.
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
/// opaque, so the glow around it is carried along but not measured.
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

let S = 1024
let ctx = CGContext(
    data: nil, width: S, height: S, bitsPerComponent: 8,
    bytesPerRow: 0, space: rgb, bitmapInfo: rgba
)!

// Apple's macOS grid: an 824-pt tile centred in the canvas, which leaves room
// for the shadow below it.
let tile = CGRect(x: 100, y: 100, width: 824, height: 824)
let shape = CGPath(roundedRect: tile, cornerWidth: 185, cornerHeight: 185, transform: nil)

ctx.saveGState()
ctx.setShadow(offset: CGSize(width: 0, height: -12), blur: 28,
              color: CGColor(gray: 0, alpha: 0.4))
ctx.addPath(shape)
ctx.setFillColor(CGColor(gray: 0, alpha: 1))
ctx.fillPath()
ctx.restoreGState()

ctx.saveGState()
ctx.addPath(shape)
ctx.clip()
// Onyx: near-black, a touch lighter at the top.
let stone = CGGradient(colorsSpace: rgb, colors: [
    CGColor(srgbRed: 0.13, green: 0.12, blue: 0.12, alpha: 1),
    CGColor(srgbRed: 0.02, green: 0.02, blue: 0.02, alpha: 1),
] as CFArray, locations: [0, 1])!
ctx.drawLinearGradient(stone, start: CGPoint(x: 0, y: tile.maxY),
                       end: CGPoint(x: 0, y: tile.minY), options: [])
// Red bloom behind the gem.
let centre = CGPoint(x: tile.midX, y: tile.midY)
let bloom = CGGradient(colorsSpace: rgb, colors: [
    CGColor(srgbRed: 0.85, green: 0.04, blue: 0.04, alpha: 0.38),
    CGColor(srgbRed: 0.85, green: 0.04, blue: 0.04, alpha: 0),
] as CFArray, locations: [0, 1])!
ctx.drawRadialGradient(bloom, startCenter: centre, startRadius: 0,
                       endCenter: centre, endRadius: 430, options: [])

// The gem, scaled so the stone fills ~74% of the tile's height and centred
// on it; its glow draws too and is clipped at the tile's edge.
let body = bodyBounds(of: gem)
let scale = tile.height * 0.74 / body.height
let origin = CGPoint(x: centre.x - body.midX * scale, y: centre.y - body.midY * scale)
ctx.interpolationQuality = .high
ctx.draw(gem, in: CGRect(x: origin.x, y: origin.y,
                         width: CGFloat(gem.width) * scale, height: CGFloat(gem.height) * scale))

// A hairline rim so the tile keeps its edge on a dark Dock.
ctx.addPath(shape)
ctx.setStrokeColor(CGColor(gray: 1, alpha: 0.10))
ctx.setLineWidth(3)
ctx.strokePath()
ctx.restoreGState()

guard let image = ctx.makeImage(),
      let png = NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]) else {
    FileHandle.standardError.write("icon render failed\n".data(using: .utf8)!)
    exit(1)
}
try! png.write(to: URL(fileURLWithPath: args[1]))
