import AppKit
import ClaudeSyncCore
import SwiftUI

/// Opens SwiftUI views in ordinary windows, one per id, and brings the app forward.
@MainActor
enum Windows {
    private static var open: [String: NSWindow] = [:]
    /// Each window's title, made again when the language changes.
    private static var titles: [String: () -> String] = [:]

    /// `replace: false` keeps an open window's view (and its state) as it is.
    static func show<V: View>(_ id: String, title: @escaping @autoclosure () -> String,
                              size: NSSize = NSSize(width: 560, height: 520),
                              replace: Bool = true, activate: Bool = true, @ViewBuilder content: () -> V) {
        titles[id] = title
        if let window = open[id] {
            if replace { window.contentViewController = NSHostingController(rootView: content()) }
            window.title = title()
            window.makeKeyAndOrderFront(nil)
            if activate { NSApp.activate(ignoringOtherApps: true) }
            return
        }
        let window = NSWindow(contentRect: NSRect(origin: .zero, size: size),
                              styleMask: [.titled, .closable, .resizable, .miniaturizable],
                              backing: .buffered, defer: false)
        window.title = title()
        window.isReleasedWhenClosed = false
        window.contentViewController = NSHostingController(rootView: content())
        window.setContentSize(size)
        window.center()
        open[id] = window
        window.makeKeyAndOrderFront(nil)
        if activate { NSApp.activate(ignoringOtherApps: true) }
    }

    static func isShowing(_ id: String) -> Bool { open[id]?.isVisible == true }

    static func close(_ id: String) {
        open[id]?.close()
    }

    /// The language changed: every open window's title follows (their contents redraw themselves).
    static func retitle() {
        for (id, window) in open {
            if let title = titles[id] { window.title = title() }
        }
    }

    /// Diagnostics: the window as drawn (with its title bar and any sheet), as a PNG.
    static func snapshot(_ id: String, to path: String) {
        guard let window = open[id] else { return }
        for (suffix, target) in [("", window), ("-sheet", window.attachedSheet)] {
            guard let target, let view = target.contentView?.superview ?? target.contentView,
                  let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { continue }
            view.cacheDisplay(in: view.bounds, to: rep)
            let file = suffix.isEmpty ? path : path.replacingOccurrences(of: ".png", with: suffix + ".png")
            try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: file))
        }
    }
}

struct DiffView: View {
    @Environment(AppModel.self) private var model
    let text: String

    var body: some View {
        let _ = model.language  // redraws in the new language
        ScrollView([.vertical, .horizontal]) {
            Text(text.isEmpty ? L10n.t("바뀐 내용이 없어요.", "No changes.") : text)
                .font(.system(.body, design: .monospaced))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding()
        }
        .frame(minWidth: 420, minHeight: 300)
    }
}

struct HistoryView: View {
    @Environment(AppModel.self) private var model
    let entries: [LogEntry]

    var body: some View {
        List(entries) { entry in
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(entry.subject.replacingOccurrences(of: "sync: \(entry.pc): ", with: ""))
                        .lineLimit(2)
                    Text("\(Presentation.clock(entry.time, now: Date().timeIntervalSince1970)) · \(entry.pc) · "
                         + L10n.t("파일 \(entry.files.count)개", "\(entry.files.count) file(s)"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if let s = model.status, Presentation.canSend(s) {
                    Button(L10n.t("되돌리기", "Revert")) {
                        let sha = String(entry.sha.prefix(12))
                        model.perform(L10n.t("되돌리는 중", "Reverting"), done: L10n.t("되돌렸어요", "Reverted")) { await $0.revert(sha) }
                    }
                }
            }
        }
        .overlay { if entries.isEmpty { Text(L10n.t("기록이 없어요.", "No history.")).foregroundStyle(.secondary) } }
        .frame(minWidth: 480, minHeight: 320)
    }
}

struct BlocklistView: View {
    @Environment(AppModel.self) private var model
    @State private var words: [String] = []
    @State private var newWord = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(L10n.t("이 PC에서 나가는 변경에 아래 단어가 있으면 올리지 않아요. 이 목록은 다른 PC로 동기화되지 않아요.",
                         "A change leaving this PC is not sent when it contains a word below. This list is not synced to other PCs."))
                .font(.callout)
                .foregroundStyle(.secondary)
            List(words, id: \.self) { word in
                HStack {
                    Text(word)
                    Spacer()
                    Button(L10n.t("빼기", "Remove")) { Task { await change { await $0.removeWord(word) } } }
                }
            }
            .overlay { if words.isEmpty { Text(L10n.t("금지어가 없어요.", "No blocked words.")).foregroundStyle(.secondary) } }
            HStack {
                TextField(L10n.t("회사명, 내부 도메인 등", "Company name, internal domains…"), text: $newWord)
                    .onSubmit(add)
                Button(L10n.t("추가", "Add"), action: add).disabled(newWord.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding()
        .frame(minWidth: 420, minHeight: 320)
        .task { await change { await $0.words() } }
    }

    private func add() {
        let word = newWord.trimmingCharacters(in: .whitespaces)
        guard !word.isEmpty else { return }
        newWord = ""
        Task { await change { await $0.addWord(word) } }
    }

    private func change(_ action: (EngineClient) async -> Result<WordsReply, EngineFailure>) async {
        guard let client = model.client else { return }
        guard let result = await model.exclusive(L10n.t("금지어 바꾸는 중", "Changing blocked words"), { await action(client) }) else { return }
        switch result {
        case .success(let reply): words = reply.words
        case .failure(let f): model.message = f.message
        }
        await model.refresh()
    }
}

struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @State private var confirmSending = false

    var body: some View {
        @Bindable var model = model
        Form {
            Section {
                Picker("언어 / Language", selection: $model.language) {
                    ForEach(AppLanguage.allCases) { Text($0.displayName).tag($0) }
                }
                Text(L10n.t("스킬·mod 설명이 다른 언어로 적혀 있으면 macOS 번역으로 바꿔 보여 줘요. 처음 한 번 언어 팩을 내려받을 수 있어요.",
                            "Skill and mod descriptions written in the other language are shown translated by macOS. The first time, a language pack may be downloaded."))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Section(L10n.t("동기화", "Sync")) {
                Picker(L10n.t("자동 동기화 주기", "Auto sync every"), selection: $model.intervalMinutes) {
                    ForEach(SyncScheduler.intervals, id: \.self) { Text(L10n.t("\($0)분", "\($0) min")).tag($0) }
                }
                Toggle(L10n.t("자동 동기화", "Auto sync"), isOn: Binding(
                    get: { model.status?.autoEnabled ?? true },
                    set: { on in model.perform(L10n.t("바꾸는 중", "Changing")) { await $0.setAuto(on) } }))
                Toggle(L10n.t("받기 전용", "Receive only"), isOn: Binding(
                    get: { model.status?.receiveOnly ?? false },
                    set: { on in
                        if on {
                            model.perform(L10n.t("바꾸는 중", "Changing"), done: L10n.t("받기 전용으로 바꿨어요", "Now receive only")) { await $0.setReceiveOnly(true) }
                        } else {
                            confirmSending = true
                        }
                    }))
                Text(L10n.t("켜면 다른 PC의 설정은 받고, 이 PC에서는 아무것도 올리지 않아요. 회사 PC처럼 정보가 나가면 안 되는 PC는 켜 두세요.",
                            "When on, this PC receives other PCs' settings and sends nothing. Keep it on for a PC whose information must not leave, like a work PC."))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            UpdateSection(updater: model.updater)
            Section(L10n.t("앱", "App")) {
                Toggle(L10n.t("로그인할 때 실행", "Open at login"), isOn: Binding(get: { model.launchAtLogin }, set: { model.setLaunchAtLogin($0) }))
                if let note = model.launchAtLoginNote {
                    Text(note).font(.caption).foregroundStyle(.secondary)
                }
                LabeledContent(L10n.t("이 PC", "This PC"), value: model.status?.pc ?? "-")
                LabeledContent(L10n.t("저장소", "Storage")) {
                    Button(Presentation.storage(model.status?.storage).title) { model.section = .storage }
                        .buttonStyle(.link)
                }
                LabeledContent(L10n.t("동기화 엔진", "Sync engine"),
                               value: EngineLocator.resolve(home: model.home, bundledResources: Bundle.main.resourcePath) ?? L10n.t("없음", "none"))
                    .font(.caption)
                LabeledContent(L10n.t("버전", "Version"), value: AppInfo.version)
                Button(L10n.t("설정 다시 하기…", "Set up again…")) { WizardWindow.open(model) }
            }
            if model.busy != nil || model.message != nil {
                WorkLine()
            }
        }
        .formStyle(.grouped)
        .frame(minWidth: 460, minHeight: 300)
        .confirmationDialog(L10n.t("이 PC에서 올리기를 켤까요?", "Start sending from this PC?"), isPresented: $confirmSending) {
            Button(L10n.t("올리기 켜기", "Start sending"), role: .destructive) {
                model.perform(L10n.t("바꾸는 중", "Changing"), done: L10n.t("이 PC에서도 올려요", "This PC now sends too")) { await $0.setReceiveOnly(false) }
            }
            Button(L10n.t("취소", "Cancel"), role: .cancel) {}
        } message: {
            Text(L10n.t("이 PC의 공용 설정 변경이 저장소로 올라가요. 회사 PC라면 회사 규정을 먼저 확인하세요.",
                         "Changes to shared settings on this PC will be sent to the storage. On a work PC, check your company's rules first."))
        }
    }
}

/// Settings: quiet updates from GitHub.
private struct UpdateSection: View {
    @Bindable var updater: Updater

    var body: some View {
        Section(L10n.t("업데이트", "Updates")) {
            Toggle(L10n.t("자동 업데이트", "Update automatically"), isOn: $updater.enabled)
            VStack(alignment: .leading, spacing: 6) {
                Text(Presentation.update(updater.phase, automatic: updater.enabled)).font(.callout)
                switch updater.phase.bar {
                case .part(let part)?: ProgressView(value: part)
                case .busy?: ProgressView().progressViewStyle(.linear)
                case nil: EmptyView()
                }
            }
            HStack {
                Button(L10n.t("지금 확인", "Check now")) { Task { await updater.check() } }
                    .disabled(updater.phase.bar != nil)
                if case .ready = updater.phase {
                    Button(L10n.t("설치하고 다시 시작", "Install and restart")) { updater.installNow() }
                }
            }
            Text(L10n.t("새 버전은 GitHub에서 받아 서명을 확인한 뒤 바로 설치하고 다시 시작해요. 동기화 중이거나 설정·저장소 바꾸기 창이 열려 있으면 끝난 뒤에 하고, 창이 열려 있었다면 다시 열어요. 회사 Mac처럼 GitHub에 접속할 수 없으면 확인만 실패하고 넘어가요.",
                        "New versions come from GitHub. After checking the signature, the app installs one and restarts right away (after a running sync, setup or storage change), opening its window again if one was open. A Mac that cannot reach GitHub (a work Mac) just skips the check."))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}
