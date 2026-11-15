import AppKit
import ClaudeSyncCore
import SwiftUI

enum MainSection: String, CaseIterable, Identifiable {
    case status, items, storage, blocklist, history, settings

    var id: Self { self }

    var title: String {
        switch self {
        case .status: return L10n.t("상태", "Status")
        case .items: return L10n.t("항목", "Items")
        case .storage: return L10n.t("저장소", "Storage")
        case .blocklist: return L10n.t("금지어", "Blocked words")
        case .history: return L10n.t("기록", "History")
        case .settings: return L10n.t("설정", "Settings")
        }
    }

    var symbol: String {
        switch self {
        case .status: return "arrow.triangle.2.circlepath"
        case .items: return "square.stack.3d.up"
        case .storage: return "externaldrive.connected.to.line.below"
        case .blocklist: return "nosign"
        case .history: return "clock.arrow.circlepath"
        case .settings: return "gearshape"
        }
    }
}

@MainActor
enum MainWindow {
    /// Brings the main window forward (made once; its state survives closing it).
    static func open(_ model: AppModel, at section: MainSection? = nil) {
        if let section { model.section = section }
        Windows.show("main", title: "Claude Sync", size: NSSize(width: 880, height: 600), replace: false) {
            MainView().environment(model)
        }
    }
}

/// The main window: a sidebar of sections and the chosen one.
struct MainView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        NavigationSplitView {
            List(MainSection.allCases, selection: $model.section) { section in
                Label(section.title, systemImage: section.symbol).tag(section)
            }
            .navigationSplitViewColumnWidth(min: 150, ideal: 170, max: 220)
        } detail: {
            Group {
                if !model.isConfigured {
                    NotConfiguredPane()
                } else {
                    switch model.section ?? .status {
                    case .status: StatusPane()
                    case .items: ItemsPane()
                    case .storage: StoragePane()
                    case .blocklist: BlocklistView()
                    case .history: HistoryPane()
                    case .settings: SettingsView()
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .navigationTitle((model.section ?? .status).title)
            .toolbar {
                ToolbarItem {
                    Button {
                        Task { await model.syncNow(manual: true) }
                    } label: {
                        Label(L10n.t("지금 동기화", "Sync now"), systemImage: "arrow.triangle.2.circlepath")
                    }
                    .help(L10n.t("지금 동기화", "Sync now"))
                    .disabled(!model.isConfigured || model.syncing || model.busy != nil)
                }
            }
        }
        .frame(minWidth: 760, minHeight: 520)
        .id(model.language)
    }
}

struct NotConfiguredPane: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(L10n.t("아직 이 Mac에 동기화 설정이 없어요.", "Sync is not set up on this Mac yet.")).font(.title2.bold())
            Text(L10n.t("다른 Mac의 설정을 받거나, 이 Mac을 첫 PC로 시작하세요. GitLab과 Notion 중에서 고를 수 있어요.", "Receive another Mac's settings, or start with this Mac as the first PC. GitLab or Notion, your choice."))
                .foregroundStyle(.secondary)
            Button(L10n.t("설정 시작하기…", "Set up…")) { WizardWindow.open(model) }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
        }
        .padding(28)
    }
}

struct StatusPane: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                StatusHeader(large: true)
                if let failure = model.failure {
                    Text(failure.message).foregroundStyle(.red).textSelection(.enabled)
                }
                if let s = model.status {
                    SyncControls(status: s, large: true)
                    WorkLine()
                    let storage = Presentation.storage(s.storage)
                    LabeledContent(L10n.t("저장소", "Storage")) {
                        Button(storage.detail.isEmpty ? storage.title : "\(storage.title) — \(storage.detail)") {
                            model.section = .storage
                        }
                            .buttonStyle(.link)
                    }
                    Divider()
                    if ProblemsList.any(s) {
                        Text(L10n.t("확인할 것", "Needs attention")).font(.headline)
                        ProblemsList(s: s)
                    } else {
                        Label(L10n.t("확인할 것이 없어요", "Nothing needs attention"), systemImage: "checkmark.circle").foregroundStyle(.secondary)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(24)
        }
    }
}

struct ItemsPane: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        ScrollView {
            if let s = model.status {
                ItemsList(s: s)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(24)
            }
        }
        .modifier(TranslatesDescriptions())
    }
}

struct HistoryPane: View {
    @Environment(AppModel.self) private var model
    @State private var entries: [LogEntry] = []

    var body: some View {
        HistoryView(entries: entries)
            .task(id: model.status?.lastPull) {
                guard let client = model.client else { return }
                switch await client.log(30) {
                case .success(let e): entries = e
                case .failure(let f): model.message = f.message
                }
            }
    }
}
