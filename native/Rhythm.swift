import Cocoa
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var serverProcess: Process?
    var attempts = 0
    let base = URL(string: "http://127.0.0.1:8787/")!

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let item = NSMenuItem()
        menu.addItem(item)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "О программе Ритм", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "Скрыть Ритм", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: "Завершить Ритм", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = appMenu
        let editItem = NSMenuItem(); menu.addItem(editItem)
        let edit = NSMenu(title: "Правка")
        for (title, action, key) in [("Отменить", "undo:", "z"), ("Вырезать", "cut:", "x"), ("Копировать", "copy:", "c"), ("Вставить", "paste:", "v"), ("Выбрать всё", "selectAll:", "a")] {
            edit.addItem(withTitle: title, action: Selector(action), keyEquivalent: key)
        }
        editItem.submenu = edit
        NSApp.mainMenu = menu
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1120, height: 800), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Ритм"
        window.minSize = NSSize(width: 720, height: 550)
        window.isReleasedWhenClosed = false
        window.center()
        let configuration = WKWebViewConfiguration()
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        window.contentView = webView
        webView.loadHTMLString("<html><body style='background:#fcfbf9;font-family:-apple-system;text-align:center;padding-top:200px;color:#7960ad'><div style='font-size:60px'>✳</div><h2>Ритм просыпается</h2><p style='color:#a294ab'>Запускаю только интерфейс. Модель пока отдыхает.</p></body></html>", baseURL: nil)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        ensureServer()
    }

    func ensureServer() {
        let task = URLSession.shared.dataTask(with: base.appendingPathComponent("api/chats")) { [weak self] _, response, _ in
            DispatchQueue.main.async {
                guard let self = self else { return }
                if (response as? HTTPURLResponse)?.statusCode == 200 {
                    self.webView.load(URLRequest(url: self.base))
                } else if self.attempts < 40 {
                    if self.attempts == 0 { self.startServer() }
                    self.attempts += 1
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.ensureServer() }
                } else {
                    self.webView.loadHTMLString("<html><body style='font-family:-apple-system;padding:80px'><h2>Не удалось открыть Ритм</h2><p>Закройте приложение и откройте его ещё раз.</p></body></html>", baseURL: nil)
                }
            }
        }
        task.resume()
    }

    func startServer() {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "__PYTHON__")
        process.arguments = ["__RHYTHM_ROOT__/ui/server.py"]
        let logPath = "__RHYTHM_ROOT__/runtime/ui-native.log"
        if !FileManager.default.fileExists(atPath: logPath) { FileManager.default.createFile(atPath: logPath, contents: nil, attributes: [.posixPermissions: 0o600]) }
        if let handle = FileHandle(forWritingAtPath: logPath) {
            handle.seekToEndOfFile(); process.standardOutput = handle; process.standardError = handle
        }
        do { try process.run(); serverProcess = process } catch { }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = navigationAction.request.url, url.scheme == "http" || url.scheme == "https" {
            if url.host != "127.0.0.1" || url.port != 8787 {
                NSWorkspace.shared.open(url)
                decisionHandler(.cancel)
                return
            }
        }
        decisionHandler(.allow)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) {
        // The backend drains an active request before it exits and frees resources.
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/curl")
        process.arguments = ["--silent", "--max-time", "2", "-X", "POST", "-H", "X-Planner-Client: local", "-H", "Content-Type: application/json", "-d", "{}", "http://127.0.0.1:8787/api/shutdown"]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try? process.run()
        process.waitUntilExit()
    }
}

let application = NSApplication.shared
let delegate = AppDelegate()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()
