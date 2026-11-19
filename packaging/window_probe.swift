import CoreGraphics
import Foundation

// Query only window ownership and geometry. CI does not need access to screen contents.
let pid = Int(CommandLine.arguments[1])!
let deadline = Date().addingTimeInterval(45)
while Date() < deadline {
    let options: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
    let windows = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] ?? []
    let visible = windows.contains { info in
        guard (info[kCGWindowOwnerPID as String] as? Int) == pid,
              (info[kCGWindowLayer as String] as? Int) == 0,
              let bounds = info[kCGWindowBounds as String] as? [String: Any],
              let width = bounds["Width"] as? Double, let height = bounds["Height"] as? Double
        else { return false }
        return width >= 780 && height >= 680
    }
    if visible {
        print("Installed application has a full-size project transfer window.")
        exit(0)
    }
    Thread.sleep(forTimeInterval: 0.5)
}
fputs("No project transfer window found for the installed executable.\n", stderr)
exit(1)

