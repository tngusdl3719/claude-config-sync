import AppKit
import ClaudeSyncCore
import SwiftUI

@main
struct ClaudeSyncApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        MenuBarExtra {
            MenuView().environment(delegate.model)
        } label: {
            Image(systemName: delegate.model.menuBar.symbol)
        }
        .menuBarExtraStyle(.window)
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let model = AppModel()

    func applicationDidFinishLaunching(_ notification: Notification) {
        model.start()
        // Started at login: no window (not even setup); the menu bar and the Dock open one when asked.
        if !Self.launchedAtLogin() {
            MainWindow.open(model)
            if !model.isConfigured { WizardWindow.open(model) }
        }
        let args = ProcessInfo.processInfo.arguments
        // Diagnostics: the menu's content in an ordinary window, and the 지금 동기화 action run once.
        if args.contains("--menu-window") {
            Windows.show("menu", title: "Claude Sync (diagnostics)", size: NSSize(width: 410, height: 720)) {
                MenuView().environment(self.model)
            }
        }
        // Diagnostics: each section of the main window saved as a PNG in the given folder, then quit.
        if let i = args.firstIndex(of: "--snapshot"), i + 1 < args.count {
            let dir = args[i + 1]
            Task { @MainActor in
                MainWindow.open(self.model)
                try? await Task.sleep(for: .seconds(5))
                for section in MainSection.allCases {
                    self.model.section = section
                    try? await Task.sleep(for: .seconds(2))
                    Windows.snapshot("main", to: "\(dir)/\(section.rawValue).png")
                }
                for mode in [StorageChangeView.Mode.move, .reconnect] {
                    Windows.show("storage-\(mode.rawValue)", title: L10n.t("저장소", "Storage"), size: NSSize(width: 540, height: 480)) {
                        StorageChangeView(mode: mode).environment(self.model)
                    }
                    try? await Task.sleep(for: .seconds(1))
                    Windows.snapshot("storage-\(mode.rawValue)", to: "\(dir)/storage-\(mode.rawValue).png")
                }
                NSApp.terminate(nil)
            }
        }
        if args.contains("--press-sync") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
                Task { await self.model.syncNow(manual: true) }
            }
        }
    }

    /// Closing the window keeps the app (and automatic sync) running.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    /// The Dock icon brings the main window back, even when another window (a diff, history) is open.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        MainWindow.open(model)
        return false
    }

    private static func launchedAtLogin() -> Bool {
        guard let event = NSAppleEventManager.shared().currentAppleEvent else { return false }
        return event.eventID == kAEOpenApplication
            && event.paramDescriptor(forKeyword: keyAEPropData)?.enumCodeValue == keyAELaunchedAsLogInItem
    }
}
