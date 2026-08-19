import AppKit

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
// .accessory — без иконки в Dock; то же самое объявлено в Info.plist через LSUIElement.
app.setActivationPolicy(.accessory)
app.run()
