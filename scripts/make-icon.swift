// Draws the app icon (two sync arrows on a blue-teal tile) and writes an .icns with iconutil.
//   swift scripts/make-icon.swift app/Resources/AppIcon.icns
// Everything is drawn here (no SF Symbols: their license excludes app icons).
import AppKit

let out = CommandLine.arguments.dropFirst().first ?? "AppIcon.icns"

func draw(_ size: Int) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8,
                               samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                               bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let k = CGFloat(size) / 1024
    let ctx = NSGraphicsContext.current!.cgContext
    ctx.scaleBy(x: k, y: k)

    // The tile: macOS icon grid (824 pt body, 100 pt margin), soft shadow, vertical gradient.
    let tile = NSBezierPath(roundedRect: NSRect(x: 100, y: 100, width: 824, height: 824), xRadius: 185, yRadius: 185)
    NSGraphicsContext.saveGraphicsState()
    let shadow = NSShadow()
    shadow.shadowColor = NSColor.black.withAlphaComponent(0.28)
    shadow.shadowBlurRadius = 24
    shadow.shadowOffset = NSSize(width: 0, height: -10)
    shadow.set()
    NSColor(srgbRed: 0.10, green: 0.42, blue: 0.80, alpha: 1).setFill()
    tile.fill()
    NSGraphicsContext.restoreGraphicsState()
    NSGradient(starting: NSColor(srgbRed: 0.24, green: 0.56, blue: 0.98, alpha: 1),
               ending: NSColor(srgbRed: 0.04, green: 0.58, blue: 0.53, alpha: 1))!.draw(in: tile, angle: -90)

    // Two arcs chasing each other, each ending in an arrowhead (counterclockwise).
    let center = NSPoint(x: 512, y: 512)
    let radius: CGFloat = 235
    NSColor.white.setStroke()
    NSColor.white.setFill()
    for (start, end) in [(CGFloat(25), CGFloat(150)), (CGFloat(205), CGFloat(330))] {
        let arc = NSBezierPath()
        arc.appendArc(withCenter: center, radius: radius, startAngle: start, endAngle: end, clockwise: false)
        arc.lineWidth = 86
        arc.lineCapStyle = .round
        arc.stroke()
        let a = end * .pi / 180
        let p = NSPoint(x: center.x + radius * cos(a), y: center.y + radius * sin(a))
        let t = NSPoint(x: -sin(a), y: cos(a))  // direction of travel
        let n = NSPoint(x: cos(a), y: sin(a))   // outward
        let head = NSBezierPath()
        head.move(to: NSPoint(x: p.x + t.x * 110, y: p.y + t.y * 110))
        head.line(to: NSPoint(x: p.x + n.x * 95 - t.x * 15, y: p.y + n.y * 95 - t.y * 15))
        head.line(to: NSPoint(x: p.x - n.x * 95 - t.x * 15, y: p.y - n.y * 95 - t.y * 15))
        head.close()
        head.lineJoinStyle = .round
        head.lineWidth = 18
        head.fill()
        head.stroke()
    }
    // A small dot in the middle: the shared settings both sides keep.
    NSBezierPath(ovalIn: NSRect(x: 512 - 52, y: 512 - 52, width: 104, height: 104)).fill()

    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

let iconset = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("AppIcon-\(UUID().uuidString).iconset")
try FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: true)
for base in [16, 32, 128, 256, 512] {
    try draw(base).write(to: iconset.appendingPathComponent("icon_\(base)x\(base).png"))
    try draw(base * 2).write(to: iconset.appendingPathComponent("icon_\(base)x\(base)@2x.png"))
}
let p = Process()
p.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
p.arguments = ["-c", "icns", iconset.path, "-o", out]
try p.run()
p.waitUntilExit()
try? FileManager.default.removeItem(at: iconset)
guard p.terminationStatus == 0 else { fatalError("iconutil failed") }
print("icon: \(out)")
