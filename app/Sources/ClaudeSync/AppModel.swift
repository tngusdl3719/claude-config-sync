import AppKit
import ClaudeSyncCore
import Foundation
import Observation
import ServiceManagement
@preconcurrency import UserNotifications

/// Everything the windows show, and every action, through the engine.
@MainActor @Observable
final class AppModel {
    let home = NSHomeDirectory()
    var status: Status?
    var failure: EngineFailure?
    /// The last message, made again in the current language whenever it is shown.
    private var messageText: (() -> String)?
    /// What the running engine change is called (shown while it runs), also made on demand.
    private var busyText: (() -> String)?
    var message: String? {
        get { messageText?() }
        set { messageText = newValue.map { text in { text } } }  // as written (the engine's own words)
    }

    /// A message in the app's words, so it follows the language when that changes.
    func say(_ text: @escaping @autoclosure () -> String) {
        messageText = text
    }
    /// The main window's section.
    var section: MainSection? = .status
    /// The app's language (settings); every window redraws when it changes.
    var language: AppLanguage {
        didSet {
            L10n.current = language
            UserDefaults.standard.set(language.rawValue, forKey: "appLanguage")
            Windows.retitle()
        }
    }
    /// Skill and mod descriptions translated into the app's language (macOS translation), kept across launches.
    private(set) var translations: TranslationCache
    private(set) var gate = WorkGate()
    private(set) var scheduler: SyncScheduler
    private var notified: Set<String> = []
    private var timer: Timer?

    init() {
        let saved = UserDefaults.standard.integer(forKey: "syncIntervalMinutes")
        scheduler = SyncScheduler(intervalMinutes: saved == 0 ? 15 : saved)
        language = UserDefaults.standard.string(forKey: "appLanguage").flatMap(AppLanguage.init(rawValue:)) ?? .fromSystem()
        translations = TranslationCache(
            dictionary: UserDefaults.standard.dictionary(forKey: "descriptionTranslations") as? [String: String] ?? [:])
        L10n.current = language
    }

    // MARK: descriptions

    /// A skill or mod description in the app's language when a translation is kept, else as written.
    func describe(_ text: String) -> String {
        translations.text(for: text, in: language)
    }

    /// Descriptions in the other language (Korean ↔ English) that are not translated yet.
    var untranslated: [String] {
        guard let d = status?.descriptions else { return [] }
        let all = [d.shared.skills, d.shared.mods, d.local.skills, d.local.mods].flatMap(\.values)
        return Array(Set(all.filter { !translations.has($0, in: language) && !skipped.contains(language.rawValue + $0)
            && DescriptionLanguage.needsTranslation($0, to: language) })).sorted()
    }

    /// Descriptions macOS could not translate this run (no language pack): shown as written.
    private var skipped: Set<String> = []

    func skipTranslations(_ texts: [String], in target: AppLanguage) {
        skipped.formUnion(texts.map { target.rawValue + $0 })
    }

    func keepTranslations(_ pairs: [(String, String)], in target: AppLanguage) {
        for (original, translated) in pairs { translations.store(original, translated, in: target) }
        UserDefaults.standard.set(translations.dictionary, forKey: "descriptionTranslations")
    }

    var isConfigured: Bool { EngineLocator.isConfigured(home: home) }

    var client: EngineClient? {
        EngineLocator.resolve(home: home, bundledResources: Bundle.main.resourcePath)
            .map { EngineClient(runner: EngineRunner(executable: $0)) }
    }

    var menuBar: MenuBarState { Presentation.menuBar(status, syncing: syncing, failure: failure) }

    /// The engine change in progress other than a sync (shown in the footer), and whether a sync runs.
    var busy: String? { gate.syncing || gate.current == nil ? nil : busyText?() ?? gate.current }
    var syncing: Bool { gate.syncing }

    var intervalMinutes: Int {
        get { scheduler.intervalMinutes }
        set {
            UserDefaults.standard.set(newValue, forKey: "syncIntervalMinutes")
            scheduler.setInterval(newValue)
        }
    }

    /// Runs one engine change while no other runs (sync, fix, setup, blocklist edit); nil when refused.
    func exclusive<T>(_ label: @escaping @autoclosure () -> String, _ work: () async -> T) async -> T? {
        guard begin(label) else { return nil }
        defer { end() }
        return await work()
    }

    private func begin(_ label: @escaping () -> String) -> Bool {
        guard gate.begin(label()) else {
            say(L10n.t("다른 작업이 진행 중이에요", "Another task is running"))
            return false
        }
        busyText = label
        return true
    }

    private func end() {
        gate.end()
        busyText = nil
    }

    // MARK: running

    func start() {
        // Status first, so the launch sync knows whether automatic sync is on.
        Task {
            await refresh()
            await autoSyncIfDue()
        }
        timer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            Task { @MainActor in
                await self?.refresh()
                await self?.autoSyncIfDue()
            }
        }
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didWakeNotification, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor in await self?.syncNow(manual: false) }
        }
    }

    func refresh() async {
        guard isConfigured, let client else {
            status = nil
            return
        }
        switch await client.status() {
        case .success(let s):
            apply(s)
        case .failure(let f):
            failure = f
        }
    }

    private func apply(_ s: Status) {
        status = s
        failure = nil
        notifyIfNew(s)
    }

    func autoSyncIfDue() async {
        guard let s = status, scheduler.isDue(now: Date(), autoEnabled: s.autoEnabled) else { return }
        await syncNow(manual: false)
    }

    func syncNow(manual: Bool) async {
        guard isConfigured, let client else { return }
        guard gate.begin(WorkGate.sync) else {
            if manual {
                let syncing = gate.syncing
                say(syncing ? L10n.t("이미 동기화 중이에요", "Already syncing") : L10n.t("다른 작업이 진행 중이에요", "Another task is running"))
            }
            return
        }
        _ = scheduler.begin(now: Date())
        defer {
            gate.end()
            scheduler.end()
        }
        switch await client.sync(auto: !manual) {
        case .success(let s):
            apply(s)
            if manual {
                let result = s.lastResult
                say(result == .ok ? L10n.t("동기화했어요", "Synced") : Presentation.label(result))
            }
        case .failure(let f):
            failure = f
            message = f.message
        }
    }

    /// One mutating action at a time; the status it prints replaces ours.
    func perform(_ label: @escaping @autoclosure () -> String, done: @escaping @autoclosure () -> String? = nil,
                 _ action: @escaping (EngineClient) async -> Result<Status, EngineFailure>) {
        guard let client else { return }
        guard begin(label) else { return }
        Task {
            let result = await action(client)
            end()
            switch result {
            case .success(let s):
                apply(s)
                messageText = done() == nil ? nil : { done() ?? "" }
            case .failure(let f):
                message = f.message
            }
        }
    }

    func performSimple(_ label: @escaping @autoclosure () -> String, done: @escaping @autoclosure () -> String,
                       _ action: @escaping (EngineClient) async -> Result<EngineOK, EngineFailure>) {
        guard let client else { return }
        guard begin(label) else { return }
        Task {
            let result = await action(client)
            end()
            switch result {
            case .success:
                say(done())
                await refresh()
            case .failure(let f):
                message = f.message
            }
        }
    }

    /// Moves this PC to another storage, or connects it again; nil when done, else what went wrong.
    /// A sync follows, so the new storage gets (or gives) the shared settings right away.
    func changeStorage(done: @escaping @autoclosure () -> String,
                       _ action: @escaping (EngineClient) async -> Result<EngineOK, EngineFailure>) async -> String? {
        guard let client else { return L10n.t("앱 안의 동기화 엔진을 찾지 못했어요. 앱을 다시 설치해 주세요.", "The sync engine inside the app is missing. Please reinstall the app.") }
        switch await exclusive(L10n.t("저장소 바꾸는 중", "Changing storage"), { await action(client) }) {
        case nil:
            return L10n.t("다른 작업이 끝난 뒤 다시 해 주세요. 토큰이나 비밀 키도 다시 넣어 주세요.", "Try again when the other task is done, and enter the token or secret key again.")
        case .failure(let f)?:
            return f.message
        case .success?:
            await refresh()
            await syncNow(manual: true)
            say(done())
            return nil
        }
    }

    // MARK: notifications

    private func notifyIfNew(_ s: Status) {
        let key: String?
        switch s.lastResult {
        case .blocked: key = "blocked:" + s.blocked.map { "\($0.file):\($0.line)" }.joined(separator: ",")
        case .conflict: key = "conflict:" + s.conflicts.map(\.id).joined(separator: ",")
        case .needsApproval: key = "approval:" + (s.pendingApproval.first?.remote ?? "")
        default: key = nil
        }
        guard let key, !notified.contains(key), Bundle.main.bundleIdentifier != nil else { return }
        notified.insert(key)
        let content = UNMutableNotificationContent()
        content.title = "Claude Sync"
        content.body = Presentation.menuBar(s, syncing: false, failure: nil).headline
        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.alert, .sound]) { granted, _ in
            guard granted else { return }
            center.add(UNNotificationRequest(identifier: key, content: content, trigger: nil))
        }
    }

    // MARK: login item

    var launchAtLogin: Bool { SMAppService.mainApp.status == .enabled }

    var launchAtLoginNote: String? {
        switch SMAppService.mainApp.status {
        case .requiresApproval: return L10n.t("시스템 설정 → 일반 → 로그인 항목에서 허용해 주세요", "Allow it in System Settings → General → Login Items")
        case .notFound: return L10n.t("응용 프로그램 폴더로 옮긴 뒤에 켤 수 있어요", "Move the app to Applications first")
        default: return nil
        }
    }

    func setLaunchAtLogin(_ on: Bool) {
        do {
            if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
            message = nil
        } catch {
            let reason = error.localizedDescription
            say(L10n.t("로그인 항목을 바꾸지 못했어요: ", "Could not change the login item: ") + reason)
        }
    }
}
