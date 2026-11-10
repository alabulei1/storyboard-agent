// Minimal macOS Vision OCR: prints one JSON line per image with text boxes
// (normalized, origin bottom-left). Usage: ocr <image> [<image> ...]
import Foundation
import Vision
import AppKit

for path in CommandLine.arguments.dropFirst() {
    guard let img = NSImage(contentsOfFile: path),
          let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        print("{\"image\":\"\(path)\",\"error\":\"unreadable\"}")
        continue
    }
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = ["zh-Hans", "en-US"]
    req.usesLanguageCorrection = false
    try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
    var items: [[String: Any]] = []
    for obs in req.results ?? [] {
        guard let top = obs.topCandidates(1).first else { continue }
        let b = obs.boundingBox
        items.append(["text": top.string, "conf": top.confidence,
                      "x": b.origin.x, "y": b.origin.y, "w": b.width, "h": b.height])
    }
    let data = try! JSONSerialization.data(withJSONObject: ["image": path, "items": items])
    print(String(data: data, encoding: .utf8)!)
}
