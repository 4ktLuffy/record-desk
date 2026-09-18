import Foundation
import Vision
import AppKit
import PDFKit

func recognize(_ image: CGImage) throws -> String {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    try VNImageRequestHandler(cgImage: image).perform([request])
    return (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
}
let url = URL(fileURLWithPath: CommandLine.arguments[1])
if CommandLine.arguments.count == 4 && CommandLine.arguments[2] == "--split" {
    guard let doc = PDFDocument(url: url), doc.pageCount <= 200 else { fatalError("Split limit is 200 pages") }
    let folder = URL(fileURLWithPath: CommandLine.arguments[3])
    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    var paths = [String]()
    for i in 0..<doc.pageCount {
        let single = PDFDocument()
        guard let page = doc.page(at: i) else { continue }
        single.insert(page, at: 0)
        let dest = folder.appendingPathComponent(String(format: "page-%03d.pdf", i+1))
        guard single.write(to: dest) else { fatalError("Could not split PDF") }
        paths.append(dest.path)
    }
    FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: ["paths": paths]))
    exit(0)
}
var pages = [String]()
if url.pathExtension.lowercased() == "pdf" {
    guard let doc = PDFDocument(url: url), doc.pageCount > 0 else { fatalError("Cannot open PDF") }
    if doc.pageCount > 20 { fatalError("PDF exceeds 20 page limit") }
    for i in 0..<doc.pageCount {
        guard let page = doc.page(at: i) else { continue }
        let text = page.string ?? ""
        if text.trimmingCharacters(in: .whitespacesAndNewlines).count > 80 {
            pages.append(text)
        } else {
            let image = page.thumbnail(of: NSSize(width: 2000, height: 2800), for: .mediaBox)
            var rect = NSRect(origin: .zero, size: image.size)
            guard let cg = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else { fatalError("Cannot render page") }
            pages.append(try recognize(cg))
        }
    }
} else {
    guard let image = NSImage(contentsOf: url) else { fatalError("Cannot open image") }
    var rect = NSRect(origin: .zero, size: image.size)
    guard let cg = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else { fatalError("Cannot read image") }
    pages.append(try recognize(cg))
}
let output = try JSONSerialization.data(withJSONObject: ["pages": pages])
FileHandle.standardOutput.write(output)
