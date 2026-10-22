// Draws the omso Builder app icon (1024×1024 PNG): a suite-style knob with a gradient value
// arc on a graphite tile, plus an orange hammer badge. Colours follow shared/ui's dark
// SurfacePalette and AccentTheme. Run by scripts/build-app.sh, which builds AppIcon.icns.
import AppKit

let size: CGFloat = 1024
let out = CommandLine.arguments[1]
guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size), pixelsHigh: Int(size), bitsPerSample: 8,
                                 samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0),
      let context = NSGraphicsContext(bitmapImageRep: rep) else { fatalError("no context") }
NSGraphicsContext.current = context
let cg = context.cgContext
let deg = CGFloat.pi / 180

func channels(_ hex: UInt32) -> [CGFloat] { [16, 8, 0].map { CGFloat((hex >> $0) & 0xff) / 255 } }
func rgb(_ hex: UInt32) -> CGColor { let c = channels(hex); return CGColor(red: c[0], green: c[1], blue: c[2], alpha: 1) }
func vertical(_ top: UInt32, _ bottom: UInt32, in rect: CGRect) {
    let gradient = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(), colors: [rgb(top), rgb(bottom)] as CFArray, locations: [0, 1])!
    cg.drawLinearGradient(gradient, start: CGPoint(x: rect.midX, y: rect.maxY), end: CGPoint(x: rect.midX, y: rect.minY), options: [])
}
func disc(_ c: CGPoint, _ r: CGFloat) -> CGRect { CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r) }

// macOS icon grid: the tile is inset from the canvas and has rounded corners.
let inset: CGFloat = 100
let tileRect = CGRect(x: inset, y: inset, width: size - 2 * inset, height: size - 2 * inset)
let tile = CGPath(roundedRect: tileRect, cornerWidth: 185, cornerHeight: 185, transform: nil)
cg.saveGState()
cg.setShadow(offset: CGSize(width: 0, height: -12), blur: 28, color: CGColor(gray: 0, alpha: 0.4))
cg.addPath(tile); cg.setFillColor(rgb(0x1d1f1f)); cg.fillPath()
cg.restoreGState()
cg.saveGState()
cg.addPath(tile); cg.clip()
vertical(0x34373a, 0x1d1f1f, in: tileRect)
cg.restoreGState()
cg.addPath(CGPath(roundedRect: tileRect.insetBy(dx: 3, dy: 3), cornerWidth: 182, cornerHeight: 182, transform: nil))
cg.setStrokeColor(CGColor(gray: 1, alpha: 0.08)); cg.setLineWidth(6); cg.strokePath()

// Knob: dark track, gradient value arc (cyan → violet → peach), flat body and pointer.
let knob = CGPoint(x: 470, y: 560)
let ringR: CGFloat = 262, ringW: CGFloat = 62
let arcStart = 225 * deg, arcEnd = -45 * deg
let value = arcStart + (arcEnd - arcStart) * 0.78
let arcColours: [UInt32] = [0x8dd9e7, 0xa1bcf4, 0xbda5ed, 0xe5a6c8, 0xecb19a]
func arcColour(_ t: CGFloat) -> CGColor {
    let x = max(0, min(1, t)) * CGFloat(arcColours.count - 1), i = min(Int(x), arcColours.count - 2), f = x - CGFloat(i)
    let a = channels(arcColours[i]), b = channels(arcColours[i + 1])
    return CGColor(red: a[0] + (b[0] - a[0]) * f, green: a[1] + (b[1] - a[1]) * f, blue: a[2] + (b[2] - a[2]) * f, alpha: 1)
}
cg.setLineCap(.round); cg.setLineWidth(ringW); cg.setStrokeColor(rgb(0x3a3e40))
cg.addArc(center: knob, radius: ringR, startAngle: arcStart, endAngle: arcEnd, clockwise: true); cg.strokePath()
cg.setLineCap(.butt)
let steps = 240
for step in 0..<steps {
    let t = CGFloat(step) / CGFloat(steps)
    let a0 = arcStart + (value - arcStart) * t, a1 = arcStart + (value - arcStart) * (t + 1.2 / CGFloat(steps))
    cg.setStrokeColor(arcColour(t))
    cg.addArc(center: knob, radius: ringR, startAngle: a0, endAngle: a1, clockwise: true); cg.strokePath()
}
for (angle, t) in [(arcStart, CGFloat(0)), (value, CGFloat(1))] {
    cg.setFillColor(arcColour(t))
    cg.fillEllipse(in: disc(CGPoint(x: knob.x + ringR * cos(angle), y: knob.y + ringR * sin(angle)), ringW / 2))
}
let bodyR: CGFloat = 196
cg.saveGState()
cg.setShadow(offset: CGSize(width: 0, height: -10), blur: 26, color: CGColor(gray: 0, alpha: 0.5))
cg.setFillColor(rgb(0x2b2d2d)); cg.fillEllipse(in: disc(knob, bodyR))
cg.restoreGState()
cg.saveGState()
cg.addEllipse(in: disc(knob, bodyR)); cg.clip()
vertical(0x3c4042, 0x26292a, in: disc(knob, bodyR))
cg.restoreGState()
cg.setLineCap(.round); cg.setLineWidth(36); cg.setStrokeColor(rgb(0xeef2f5))
cg.move(to: CGPoint(x: knob.x + 95 * cos(value), y: knob.y + 95 * sin(value)))
cg.addLine(to: CGPoint(x: knob.x + 160 * cos(value), y: knob.y + 160 * sin(value)))
cg.strokePath()

// Badge: orange disc separated from the knob by a graphite gap.
let badge = CGPoint(x: 718, y: 312), badgeR: CGFloat = 150
cg.setFillColor(rgb(0x232526)); cg.fillEllipse(in: disc(badge, badgeR + 22))
cg.saveGState()
cg.setShadow(offset: CGSize(width: 0, height: -8), blur: 20, color: CGColor(gray: 0, alpha: 0.45))
cg.setFillColor(rgb(0xefa264)); cg.fillEllipse(in: disc(badge, badgeR))
cg.restoreGState()
cg.saveGState()
cg.addEllipse(in: disc(badge, badgeR)); cg.clip()
vertical(0xf4bd86, 0xe8914f, in: disc(badge, badgeR))
cg.restoreGState()

// Claw hammer, drawn upright in unit space and tilted so the handle points down-left.
// Each part is filled separately so overlapping subpaths cannot cancel out.
cg.saveGState()
cg.translateBy(x: badge.x, y: badge.y)
cg.rotate(by: -45 * deg)
cg.scaleBy(x: 128, y: 128)
cg.translateBy(x: -0.02, y: 0.19)
cg.setFillColor(rgb(0x1d1f1f))
let handle = (CGRect(x: -0.12, y: -1.0, width: 0.24, height: 1.24), CGFloat(0.12))
let head = (CGRect(x: -0.60, y: 0.16, width: 0.80, height: 0.40), CGFloat(0.06))
let face = (CGRect(x: -0.72, y: 0.10, width: 0.26, height: 0.52), CGFloat(0.07))
for (part, radius) in [handle, head, face] {
    cg.addPath(CGPath(roundedRect: part, cornerWidth: radius, cornerHeight: radius, transform: nil)); cg.fillPath()
}
cg.move(to: CGPoint(x: 0.12, y: 0.56))
cg.addQuadCurve(to: CGPoint(x: 0.68, y: 0.12), control: CGPoint(x: 0.58, y: 0.58))
cg.addQuadCurve(to: CGPoint(x: 0.12, y: 0.18), control: CGPoint(x: 0.42, y: 0.30))
cg.closePath(); cg.fillPath()
cg.restoreGState()

NSGraphicsContext.current = nil
try rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out))
