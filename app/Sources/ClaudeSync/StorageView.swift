import AppKit
import ClaudeSyncCore
import SwiftUI

/// Where this PC's shared history lives: GitLab, GitHub and Notion listed, the one in use switched on.
/// Switching another on moves this PC there; the one in use can be connected again.
struct StoragePane: View {
    @Environment(AppModel.self) private var model
    @State private var change: StorageChangeView.Mode?

    var body: some View {
        let storage = model.status?.storage
        let current = storage.map(StorageKind.of)
        let waiting = model.busy != nil || model.syncing
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text(L10n.t("한 번에 한 저장소만 켜져요. 다른 저장소를 켜면 공용 설정을 그쪽으로 옮겨요. 다른 PC도 같은 저장소를 켜야 서로 동기화돼요.",
                            "One storage is on at a time. Switching another on moves the shared settings there. Other PCs must switch on the same one to keep syncing."))
                    .font(.callout)
                    .foregroundStyle(.secondary)
                ForEach(StorageKind.allCases) { kind in
                    row(kind, on: kind == current, storage: storage, waiting: waiting)
                }
                // Other work shows on the work line below; a sync does not, so say why the switches wait.
                if model.syncing {
                    HStack(spacing: 6) {
                        ProgressView().controlSize(.small)
                        Text(L10n.t("동기화 중이에요. 끝나면 바꿀 수 있어요", "Syncing. Changes wait until it is done"))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                if model.status?.receiveOnly == true {
                    Text(L10n.t("받기 전용 PC는 아무것도 올리지 않으므로, Notion은 올리는 PC가 먼저 옮겨 간 페이지로만 옮길 수 있어요.",
                                "A receive-only PC sends nothing, so it can move only to a Notion page a sending PC has moved to first."))
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
                WorkLine()
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(24)
        }
        .sheet(item: $change) { mode in
            StorageChangeView(mode: mode).environment(model)
        }
    }

    private func row(_ kind: StorageKind, on: Bool, storage: Storage?, waiting: Bool) -> some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Label(kind.title, systemImage: kind == .notion ? "doc.text" : "server.rack").font(.title3.bold())
                    Spacer()
                    // Only switching on does something: the one in use goes off when another comes on.
                    Toggle(kind.title, isOn: Binding(get: { on }, set: { if $0 && !on { change = .move(kind) } }))
                        .toggleStyle(.switch)
                        .labelsHidden()
                        .disabled(on || waiting)
                        .help(on ? L10n.t("지금 쓰는 저장소예요. 다른 저장소를 켜면 꺼져요", "In use. It goes off when another one is switched on")
                                 : kind.moveTitle)
                }
                if on {
                    let text = Presentation.storage(storage)
                    Text(text.detail).textSelection(.enabled)
                    if let link = Self.link(storage) {
                        Link(Presentation.storageLink(storage), destination: link)
                    }
                    HStack {
                        Button(L10n.t("다시 연결…", "Connect again…")) { change = .reconnect }
                            .disabled(waiting)
                        Text(L10n.t("토큰이나 Notion 로그인을 새로 받아요. 연결이 끊겼다는 안내가 나오면 하세요.",
                                    "A new token or Notion sign-in. Use it when you are told the connection was lost."))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                } else {
                    Text(kind.blurb).font(.callout).foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(6)
        }
    }

    static func link(_ s: Storage?) -> URL? {
        guard let s else { return nil }
        if s.backend == .notion { return s.url.isEmpty ? nil : URL(string: s.url) }
        guard s.remote.hasPrefix("https://") else { return nil }
        return URL(string: s.remote.hasSuffix(".git") ? String(s.remote.dropLast(4)) : s.remote)
    }
}

/// Connect again to the same storage, or move to another one.
struct StorageChangeView: View {
    enum Mode: Identifiable, Hashable {
        case reconnect, move(StorageKind)
        var id: String {
            switch self {
            case .reconnect: return "reconnect"
            case .move(let kind): return "move-\(kind.rawValue)"
            }
        }
    }

    let mode: Mode
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    /// The app's Notion public connection; nil in a build without one (then only the secret-key way).
    private let oauthClient = NotionOAuthClient.thisMac
    @State private var kind: StorageKind = .gitlab
    private var backend: Backend { kind.backend }
    @State private var repoText = ""
    @State private var username = ""
    @State private var tokenText = ""
    @State private var pageText = ""
    @State private var useSecretKey = false
    @State private var login: NotionLogin?
    @State private var loginPage = ""
    @State private var loggingIn = false
    @State private var running = false
    @State private var problem: String?

    private var signingIn: Bool { backend == .notion && oauthClient != nil && !useSecretKey }
    /// The git host the help (new repository, token) is for: the address typed, else the storage chosen.
    private var gitKind: StorageKind { StorageKind.forAddress(repoText, chosen: kind) }
    private var moving: Bool { mode != .reconnect }

    init(mode: Mode) {
        self.mode = mode
        if case .move(let target) = mode { _kind = State(initialValue: target) }  // its title from the first frame
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(moving ? kind.moveTitle : L10n.t("다시 연결", "Connect again")).font(.title2.bold())
            if backend == .git {
                gitFields
            } else if signingIn, let oauthClient {
                NotionSignInView(
                    oauthClient: oauthClient,
                    intro: mode == .reconnect
                        ? L10n.t("Notion에 다시 로그인하고, 지금 쓰는 페이지를 골라 주세요.",
                                 "Sign in to Notion again and pick the page this PC uses.")
                        : L10n.t("Notion에 로그인하고, 옮겨 갈 페이지를 골라 주세요. 처음이라면 빈 페이지를 먼저 만들어 두세요.",
                                 "Sign in to Notion and pick the page to move to. If it is the first time, make an empty page first."),
                    login: $login, page: $loginPage, loggingIn: $loggingIn, problem: $problem,
                    useSecretKey: { useSecretKey = true })
            } else {
                secretFields
            }
            if let problem {
                Text(problem).font(.callout).foregroundStyle(.red).textSelection(.enabled)
            }
            Spacer(minLength: 0)
            HStack {
                Button(L10n.t("취소", "Cancel")) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                    .disabled(running)
                Spacer()
                if running {
                    ProgressView().controlSize(.small)
                    Text(L10n.t("연결하고 동기화하는 중…", "Connecting and syncing…")).font(.caption)
                }
                Button(mode == .reconnect ? L10n.t("다시 연결", "Connect again") : L10n.t("옮기기", "Move")) { run() }
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(.borderedProminent)
                    .disabled(running || loggingIn)
            }
        }
        .padding(22)
        .frame(width: 540, height: 480)
        .onAppear(perform: prefill)
        .onChange(of: model.language) { problem = nil }  // its words were the old language's
    }

    private var gitFields: some View {
        VStack(alignment: .leading, spacing: 8) {
            if moving && gitKind == .github {
                Text(LocalizedStringKey(L10n.t("GitHub의 **비공개** 저장소 주소예요. 비어 있거나 README만 있는 새 저장소(.gitignore·라이선스 템플릿 없이), 또는 다른 PC가 이미 쓰는 저장소로 옮길 수 있어요.",
                                                "The address of a **private** GitHub repository: a new one (empty or with only a README; no .gitignore or license template), or the one other PCs already use.")))
                    .font(.callout)
                Link(L10n.t("GitHub에서 새 저장소 만들기", "Make a new repository on GitHub"), destination: Wizard.newRepoPage(.github))
            } else if moving {
                Text(LocalizedStringKey(L10n.t("GitLab 같은 git 호스팅의 **비공개** 프로젝트 주소예요. 새로 만든다면 Settings → Repository → Protected branches 에서 main 보호를 풀어 주세요.",
                                                "The address of a **private** project on a git host such as GitLab. For a new one, unprotect main in Settings → Repository → Protected branches.")))
                    .font(.callout)
            }
            field(L10n.t("저장소 주소", "Repository address")) {
                TextField(kind == .github ? L10n.t("https://github.com/아이디/claude-config.git", "https://github.com/you/claude-config.git")
                          : L10n.t("https://gitlab.com/아이디/claude-config.git", "https://gitlab.com/you/claude-config.git"), text: $repoText)
            }
            field(L10n.t("토큰 사용자 이름 (보통 이 PC 이름)", "Token user name (usually this PC's name)")) {
                TextField("personal-mac", text: $username)
            }
            HStack {
                Button(L10n.t("토큰 만드는 페이지 열기", "Open the token page")) {
                    if case .ok(let repo) = Wizard.checkRepoURL(repoText), let url = Wizard.accessTokensPage(repo) {
                        NSWorkspace.shared.open(url)
                    }
                }
                Text(gitKind == .github
                     ? L10n.t("Fine-grained token, 이 저장소만, Contents 읽기·쓰기", "Fine-grained token, this repository only, Contents read and write")
                     : L10n.t("역할 Developer, 권한 read_repository·write_repository", "Role Developer, scopes read_repository and write_repository")).font(.caption).foregroundStyle(.secondary)
            }
            field(L10n.t("액세스 토큰", "Access token")) {
                SecureField(L10n.t("토큰을 붙여 넣으세요 (화면에 보이지 않아요)", "Paste the token (it stays hidden)"), text: $tokenText)
            }
            Text(L10n.t("토큰은 이 Mac의 키체인에만 저장돼요.", "The token is kept only in this Mac's keychain.")).font(.caption).foregroundStyle(.secondary)
        }
    }

    private var secretFields: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L10n.t("동기화 페이지에 연결(integration)을 추가하고, 그 연결의 비밀 키를 넣어 주세요.", "Add your integration to the sync page, then enter that integration's secret key.")).font(.callout)
            field(L10n.t("Notion 페이지 주소", "Notion page address")) {
                TextField("https://www.notion.so/…", text: $pageText)
            }
            field(L10n.t("비밀 키", "Secret key")) {
                SecureField(L10n.t("비밀 키를 붙여 넣으세요 (화면에 보이지 않아요)", "Paste the secret key (it stays hidden)"), text: $tokenText)
            }
            Text(L10n.t("비밀 키는 이 Mac의 키체인에만 저장돼요.", "The secret key is kept only in this Mac's keychain.")).font(.caption).foregroundStyle(.secondary)
            if oauthClient != nil {
                Button(L10n.t("Notion 로그인으로 연결하기", "Sign in with Notion instead")) { useSecretKey = false }.buttonStyle(.link)
            }
        }
    }

    private func field<F: View>(_ label: String, @ViewBuilder _ input: () -> F) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            input().textFieldStyle(.roundedBorder)
        }
    }

    private func prefill() {
        let storage = model.status?.storage
        let current = storage.map(StorageKind.of) ?? (model.status?.backend == .notion ? .notion : .gitlab)
        if case .move(let target) = mode { kind = target } else { kind = current }
        username = (storage?.username.isEmpty == false ? storage?.username : model.status?.pc) ?? ""
        guard mode == .reconnect else { return }  // moving starts from empty fields
        if current != .notion { repoText = storage?.remote ?? "" }
        if current == .notion {
            pageText = storage?.page ?? ""
            loginPage = storage?.page ?? ""
            useSecretKey = storage?.auth == "secret"
        }
    }

    private func run() {
        problem = nil
        let token = Wizard.cleanToken(tokenText)
        let reconnect = mode == .reconnect
        let action: (EngineClient) async -> Result<EngineOK, EngineFailure>
        if backend == .git {
            guard case .ok(let repo) = Wizard.checkRepoURL(repoText) else {
                if case .invalid(let why) = Wizard.checkRepoURL(repoText) { problem = why }
                return
            }
            let user = username.trimmingCharacters(in: .whitespaces)
            guard !user.isEmpty else { problem = L10n.t("토큰 사용자 이름을 넣어 주세요", "Enter the token user name"); return }
            guard !token.isEmpty else { problem = L10n.t("토큰을 붙여 넣어 주세요", "Paste the token"); return }
            action = { await $0.setGitStorage(remote: repo, username: user, token: token) }
        } else if signingIn {
            guard login != nil else { problem = L10n.t("먼저 'Notion으로 연결'을 눌러 주세요", "Click 'Connect with Notion' first"); return }
            guard !loginPage.isEmpty else { problem = L10n.t("동기화에 쓸 페이지를 골라 주세요", "Pick the page to sync with"); return }
            let page = loginPage
            action = { await $0.setNotionStorage(page: page, secret: nil) }
        } else {
            guard case .ok(let page) = Wizard.checkNotionPage(pageText) else {
                if case .invalid(let why) = Wizard.checkNotionPage(pageText) { problem = why }
                return
            }
            guard !token.isEmpty else { problem = L10n.t("비밀 키를 붙여 넣어 주세요", "Paste the secret key"); return }
            action = { await $0.setNotionStorage(page: page, secret: token) }
        }
        tokenText = ""
        running = true
        Task {
            let error = await model.changeStorage(
                done: reconnect ? L10n.t("다시 연결했어요", "Connected again") : L10n.t("저장소를 옮겼어요", "Storage changed"), action)
            running = false
            if let error { problem = error } else { dismiss() }
        }
    }
}
