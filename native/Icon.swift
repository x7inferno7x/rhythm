import Cocoa
let size = NSSize(width: 1024, height: 1024)
let image = NSImage(size: size)
image.lockFocus()
NSColor(calibratedRed: 0.48, green: 0.35, blue: 0.64, alpha: 1).setFill()
NSBezierPath(roundedRect: NSRect(x: 38,y: 38,width: 948,height: 948),xRadius: 215,yRadius: 215).fill()
let attributes: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 790,weight: .ultraLight), .foregroundColor: NSColor.white]
let text = "✳" as NSString
let bounds = text.size(withAttributes: attributes)
text.draw(at: NSPoint(x:(1024-bounds.width)/2,y:(1024-bounds.height)/2+25),withAttributes: attributes)
image.unlockFocus()
let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
try bitmap.representation(using: .png,properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
