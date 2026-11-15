import AppKit
import ClaudeSyncCore
import SwiftUI

@MainActor
enum WizardWindow {
    static func open(_ model: AppModel) {
        Windows.show("wizard", title: L10n.t("Claude Sync 설정", "Claude Sync setup"), size: NSSize(width: 580, height: 560)) {
            WizardView().environment(model)
        }
    }
}

/// First-run setup: receive another PC's settings (join) or start from this one (init).
struct WizardView: View {
    @Environment(AppModel.self) private var model

    enum Path { case join, first }
    enum Step: Int { case start, repo, token, name, words, choose, running, result }

    @State private var path: Path = .join
    @State private var step: Step = .start
    @State private var kind: StorageKind = .gitlab
    private var backend: Backend { kind.backend }
    /// The git host the repository help is for: the address typed, else the tab chosen.
    private var gitKind: StorageKind { StorageKind.forAddress(repoText, chosen: kind) }
    @State private var repoText = ""
    @State private var repo = ""
    @State private var pageText = ""
    @State private var page = ""
    @State private var tokenText = ""
    @State private var receiveOnly = false
    /// The app's Notion public connection; nil in a build without one (then only the secret-key way).
    private let oauthClient = NotionOAuthClient.thisMac
    @State private var useSecretKey = false
    @State private var login: NotionLogin?
    @State private var loginPage = ""
    @State private var loggingIn = false
    @State private var pcName = Wizard.sanitizePCName(Host.current().localizedName ?? "")
    @State private var wordsText = ""
    @State private var candidates: Candidates?
    @State private var skills: Set<String> = []
    @State private var mods: Set<String> = []
    @State private var keys: Set<String> = Set(Wizard.recommendedKeys)
    @State private var moveClaudeMd = true
    @State private var requireApproval = true
    @State private var result: JoinResult?
    @State private var problem: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            content
            if let problem {
                Text(problem).font(.callout).foregroundStyle(.red)
            }
            Spacer(minLength: 0)
            navigation
        }
        .padding(22)
        .frame(minWidth: 540, minHeight: 500)
        .id(model.language)
        .onChange(of: model.language) { problem = nil }  // its words were the old language's
    }

    @ViewBuilder private var content: some View {
        switch step {
        case .start:
            Text(L10n.t("Claude Code 설정 동기화", "Claude Code settings sync")).font(.title2.bold())
            Text(L10n.t("공용으로 지정한 지침·설정·스킬·mods만 PC끼리 주고받아요. PC 전용 항목은 밖으로 나가지 않아요.",
                        "Only the instructions, settings, skills and mods you mark as shared go between PCs. PC-only items never leave."))
                .foregroundStyle(.secondary)
            choice(L10n.t("다른 PC의 설정 받기", "Receive another PC's settings"), L10n.t("이미 다른 Mac에서 동기화를 쓰고 있어요.", "Another Mac already syncs."), .join)
            choice(L10n.t("이 PC가 처음이에요", "This is the first PC"), L10n.t("이 Mac의 설정을 공용 저장소에 처음으로 올려요.", "Send this Mac's settings to a new shared storage."), .first)
        case .repo:
            Text(L10n.t("공용 설정을 둘 곳", "Where shared settings live")).font(.title3.bold())
            Picker(L10n.t("저장소", "Storage"), selection: $kind) {
                ForEach(StorageKind.allCases) { Text($0.title).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            if backend == .git {
                // The help and the link follow the address typed (else the tab); one field, so typing is never cut off.
                Text(LocalizedStringKey(gitKind == .github
                    ? L10n.t("GitHub의 **비공개** 저장소 주소예요. 처음이라면 GitHub에서 비공개 저장소를 새로 만드세요. README는 넣어도 되지만 .gitignore·라이선스 템플릿은 고르지 마세요(일부 파일이 동기화에서 빠질 수 있어요).",
                             "The address of a **private** GitHub repository. For a first PC, make a new private repository. A README is fine, but do not pick a .gitignore or license template (some files could quietly stop syncing).")
                    : L10n.t("GitLab 같은 git 호스팅의 **비공개** 프로젝트 주소예요. 처음이라면 GitLab에서 빈 비공개 프로젝트를 만들고(README 포함), Settings → Repository → Protected branches 에서 main 보호를 풀어 주세요.",
                             "The address of a **private** project on a git host such as GitLab. For a first PC, make an empty private project (with a README) and unprotect main in Settings → Repository → Protected branches.")))
                    .font(.callout)
                TextField(kind == .github ? L10n.t("https://github.com/아이디/claude-config.git", "https://github.com/you/claude-config.git")
                          : L10n.t("https://gitlab.com/아이디/claude-config.git", "https://gitlab.com/you/claude-config.git"), text: $repoText)
                    .textFieldStyle(.roundedBorder)
                Link(gitKind == .github ? L10n.t("GitHub에서 새 저장소 만들기", "Make a new repository on GitHub")
                                        : L10n.t("GitLab에서 새 프로젝트 만들기", "Make a new project on GitLab"),
                     destination: Wizard.newRepoPage(gitKind))
            } else if signingIn {
                notionSignIn
            } else {
                VStack(alignment: .leading, spacing: 4) {
                    Text(LocalizedStringKey(L10n.t("1. 아래 버튼으로 Notion 연결(integration) 페이지를 열고, 새 **내부 연결**을 만든 뒤 비밀 키를 복사해 두세요.",
                                                    "1. Open Notion's integrations page with the button below, make a new **internal integration**, and copy its secret key.")))
                    Text(L10n.t("2. 동기화용 빈 페이지를 만들고, 오른쪽 위 … → 연결(Connections) → 방금 만든 연결을 추가하세요.",
                                    "2. Make an empty page for syncing, then add the integration from … → Connections at its top right."))
                    Text(L10n.t("3. 그 페이지의 … → 링크 복사로 받은 주소를 아래에 붙여 넣으세요.", "3. Paste the address from the page's … → Copy link below."))
                }
                .font(.callout)
                Button(L10n.t("Notion 연결 만들기", "Make a Notion integration")) { NSWorkspace.shared.open(Wizard.notionIntegrationsPage) }
                TextField("https://www.notion.so/…", text: $pageText)
                    .textFieldStyle(.roundedBorder)
                Text(L10n.t("다른 PC도 같은 페이지 주소와 같은 비밀 키를 써요.", "Other PCs use the same page address and secret key.")).font(.caption).foregroundStyle(.secondary)
                if oauthClient != nil {
                    Button(L10n.t("Notion 로그인으로 연결하기", "Sign in with Notion instead")) { useSecretKey = false }.buttonStyle(.link)
                }
            }
        case .token where backend == .notion:
            Text(L10n.t("Notion 비밀 키", "Notion secret key")).font(.title3.bold())
            Text(L10n.t("1단계에서 만든 연결의 비밀 키(ntn_로 시작)를 붙여 넣으세요.", "Paste the secret key (starting with ntn_) of the integration from step 1.")).font(.callout)
            SecureField(L10n.t("비밀 키를 붙여 넣으세요 (화면에 보이지 않아요)", "Paste the secret key (it stays hidden)"), text: $tokenText)
                .textFieldStyle(.roundedBorder)
            Text(L10n.t("비밀 키는 이 Mac의 키체인에만 저장돼요.", "The secret key is kept only in this Mac's keychain.")).font(.caption).foregroundStyle(.secondary)
        case .token:
            Text(L10n.t("이 PC 전용 액세스 토큰", "An access token for this PC")).font(.title3.bold())
            Text(L10n.t("PC마다 토큰을 따로 만들어요. 아래 버튼으로 페이지를 열고 이렇게 고르세요:", "Each PC has its own token. Open the page with the button below and choose:"))
                .font(.callout)
            VStack(alignment: .leading, spacing: 3) {
                Text(L10n.t("• 이름: ", "• Name: ") + pcName)
                if kind == .github {
                    Text(L10n.t("• 종류: Fine-grained token", "• Type: Fine-grained token"))
                    Text(L10n.t("• Repository access: Only select repositories → 이 저장소", "• Repository access: Only select repositories → this repository"))
                    Text(L10n.t("• Permissions: Contents — Read and write", "• Permissions: Contents — Read and write"))
                } else {
                    Text(L10n.t("• 역할(Role): Developer", "• Role: Developer"))
                    Text(L10n.t("• 권한(Scopes): read_repository, write_repository", "• Scopes: read_repository, write_repository"))
                }
                Text(L10n.t("• 만료일: 가장 늦은 날짜", "• Expiry: the latest date allowed"))
            }
            .font(.callout.monospaced())
            Button(L10n.t("토큰 만드는 페이지 열기", "Open the token page")) {
                if let url = Wizard.accessTokensPage(repo) { NSWorkspace.shared.open(url) }
            }
            SecureField(L10n.t("만든 토큰을 붙여 넣으세요 (화면에 보이지 않아요)", "Paste the new token (it stays hidden)"), text: $tokenText)
                .textFieldStyle(.roundedBorder)
            Text(L10n.t("토큰은 이 Mac의 키체인에만 저장돼요.", "The token is kept only in this Mac's keychain.")).font(.caption).foregroundStyle(.secondary)
        case .name:
            Text(L10n.t("이 PC의 이름", "This PC's name")).font(.title3.bold())
            Text(L10n.t("기록과 토큰 이름에 쓰여요. 영문 소문자·숫자·하이픈만 쓸 수 있어요.", "Used in the history and the token name. Lowercase letters, digits and hyphens only.")).font(.callout)
            TextField("company-mac", text: $pcName).textFieldStyle(.roundedBorder)
        case .words:
            Text(L10n.t("금지어", "Blocked words")).font(.title3.bold())
            Text(L10n.t("회사 PC라면 회사명, 내부 도메인, 서버 이름처럼 이 PC 밖으로 나가면 안 되는 단어를 적어 주세요. 공용 파일에 이 단어가 들어가면 올리지 않고 알려 드려요. 쉼표나 줄바꿈으로 구분하고, 비워 둬도 돼요.",
                            "On a work PC, list words that must not leave it, such as the company name, internal domains and server names. A shared file containing one is not sent, and you are told. Separate with commas or new lines; leaving it empty is fine."))
                .font(.callout)
            TextEditor(text: $wordsText)
                .font(.body.monospaced())
                .frame(minHeight: 120)
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(.quaternary))
            if path == .join {
                Toggle(L10n.t("받기 전용: 다른 PC의 설정은 받고, 이 PC에서는 아무것도 올리지 않아요", "Receive only: get other PCs' settings and send nothing from this PC"), isOn: $receiveOnly)
                Text(backend == .notion
                     ? L10n.t("회사 PC라면 받기 전용을 권해요. Notion으로 올리기를 켤지는 회사 규정을 보고 정하세요.",
                              "Receive only is advised on a work PC. Whether to send to Notion is for your company's rules to decide.")
                     : L10n.t("회사 PC처럼 외부로 올리기가 막혀 있거나 회사 정보가 나가면 안 되는 PC라면 켜 두세요.",
                              "Keep it on for a PC that cannot send out, or whose company information must not leave, like a work PC."))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        case .choose:
            chooser
        case .running:
            HStack(spacing: 12) {
                ProgressView()
                VStack(alignment: .leading) {
                    Text(L10n.t("설정하는 중…", "Setting up…")).font(.title3.bold())
                    Text(L10n.t("서버 연결 확인 → 공용 설정 받기 → 이 Mac에 적용 (몇 분 걸릴 수 있어요)", "Checking the server → receiving shared settings → applying them here (may take a few minutes)"))
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            }
        case .result:
            resultView
        }
    }

    /// Notion reached by signing in (spec §7), when this build has a public connection.
    private var signingIn: Bool { backend == .notion && oauthClient != nil && !useSecretKey }

    private var notionSignIn: some View {
        NotionSignInView(
            oauthClient: oauthClient!,
            intro: L10n.t("Notion에 로그인하고, 동기화에 쓸 페이지 하나를 골라 주세요. 처음이라면 빈 페이지를 먼저 만들어 두세요. 다른 PC에서도 같은 페이지를 고르면 돼요.",
                          "Sign in to Notion and pick one page to sync with. For a first PC, make an empty page first. Other PCs pick the same page."),
            login: $login, page: $loginPage, loggingIn: $loggingIn, problem: $problem,
            useSecretKey: { useSecretKey = true })
    }

    private var chooser: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L10n.t("공용으로 보낼 것", "What to share")).font(.title3.bold())
            if let c = candidates {
                ScrollView {
                    VStack(alignment: .leading, spacing: 6) {
                        if let md = c.claudeMd, md.exists {
                            Toggle(L10n.t("지침 CLAUDE.md (\(md.lines)줄)을 공용으로 옮기기", "Move the instructions CLAUDE.md (\(md.lines) lines) to shared"), isOn: $moveClaudeMd)
                        }
                        Text(L10n.t("스킬", "Skills")).font(.headline)
                        ForEach(c.skills, id: \.self) { toggle($0, in: $skills) }
                        Text("mods").font(.headline)
                        ForEach(c.mods, id: \.self) { toggle($0, in: $mods) }
                        Text(L10n.t("설정", "Settings")).font(.headline)
                        ForEach(c.keys) { key in
                            Toggle(isOn: member(key.key, in: $keys)) {
                                Text(key.key) + Text(key.valuePreview.map { "  \($0)" } ?? "").foregroundColor(.secondary)
                            }
                        }
                        Toggle(L10n.t("다른 PC가 바꾼 실행 파일은 이 PC에서 확인한 뒤 적용", "Check executables changed by other PCs before applying them here"), isOn: $requireApproval)
                    }
                }
                .frame(maxHeight: 300)
            } else {
                ProgressView(L10n.t("이 Mac의 스킬·mods·설정을 읽는 중…", "Reading this Mac's skills, mods and settings…"))
            }
        }
        .task(id: step) {
            guard step == .choose, candidates == nil, let client = model.client else { return }
            switch await client.candidates() {
            case .success(let c):
                candidates = c
                skills = Set(c.skills)
                mods = Set(c.mods)
                keys = Set(c.keys.map(\.key)).intersection(Wizard.recommendedKeys).union(keys.intersection(c.keys.map(\.key)))
            case .failure(let f):
                problem = f.message
            }
        }
    }

    private var resultView: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let r = result, r.result == "ok" || r.error == nil {
                Label(L10n.t("설정을 마쳤어요", "Setup is done"), systemImage: "checkmark.circle.fill").font(.title2.bold()).foregroundStyle(.green)
            } else {
                Label(L10n.t("설정 중에 문제가 있었어요", "Setup ran into a problem"), systemImage: "exclamationmark.triangle.fill").font(.title2.bold()).foregroundStyle(.orange)
            }
            if let r = result {
                if let error = r.error { Text(error).foregroundStyle(.red) }
                ForEach(Array(r.overwritten.enumerated()), id: \.offset) { _, o in
                    Text(L10n.t("공용 값으로 바뀐 설정: ", "Setting now the shared value: ") + "\(o.path.joined(separator: ".")) \(o.local?.display ?? "") → \(o.shared?.display ?? "")").font(.callout)
                }
                ForEach(Array(r.collisions.enumerated()), id: \.offset) { _, c in
                    Text(L10n.t("이름이 겹쳐 건너뛴 공용 항목: ", "Shared item skipped for a name clash: ") + "\(c.kind)/\(c.name)").font(.callout)
                }
                ForEach(r.missingPlugins, id: \.self) { Text(L10n.t("이 Mac에 없는 플러그인 (직접 설치 필요): ", "Plugin missing on this Mac (install it yourself): ") + $0).font(.callout) }
                ForEach(r.managedSettingsKeys, id: \.self) { Text(L10n.t("회사 관리 설정이 정한 항목 (mod가 막힐 수 있음): ", "Set by managed (company) settings, mods may be blocked: ") + $0).font(.callout) }
                if let backup = r.backup { Text(L10n.t("백업: ", "Backup: ") + backup).font(.caption).foregroundStyle(.secondary) }
            }
            Divider()
            Text(L10n.t("Claude Code를 다시 시작하면 입력창 위에 동기화 상태가 보이고 /sync 도 쓸 수 있어요.", "Restart Claude Code to see the sync status above the prompt and use /sync."))
            Text(L10n.t("작업 후 Codex 리뷰 지침을 쓰려면 이 Mac에도 codex 설치와 codex login 이 필요해요.", "To follow the Codex review instructions, install codex on this Mac and run codex login.")).foregroundStyle(.secondary)
        }
    }

    private var navigation: some View {
        HStack {
            if step != .start && step != .running && step != .result {
                Button(L10n.t("이전", "Back")) { back() }
            }
            Spacer()
            switch step {
            case .start: EmptyView()
            case .running: EmptyView()
            case .result:
                Button(L10n.t("닫기", "Close")) {
                    Task { await model.refresh() }
                    Windows.close("wizard")
                }
                .keyboardShortcut(.defaultAction)
            default:
                Button(step == lastInputStep ? L10n.t("설정 시작", "Start setup") : L10n.t("다음", "Next")) { next() }
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(.borderedProminent)
            }
        }
    }

    private var lastInputStep: Step { path == .first ? .choose : .words }

    private func choice(_ title: String, _ detail: String, _ p: Path) -> some View {
        Button {
            path = p
            requireApproval = p == .first
            step = .repo
        } label: {
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.headline)
                Text(detail).font(.callout).foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(12)
        }
        .buttonStyle(.bordered)
    }

    private func toggle(_ name: String, in set: Binding<Set<String>>) -> some View {
        Toggle(name, isOn: member(name, in: set))
    }

    private func member(_ name: String, in set: Binding<Set<String>>) -> Binding<Bool> {
        Binding(get: { set.wrappedValue.contains(name) },
                set: { on in if on { set.wrappedValue.insert(name) } else { set.wrappedValue.remove(name) } })
    }

    private func back() {
        problem = nil
        switch step {
        case .repo: step = .start
        case .token: step = .repo
        case .name: step = signingIn ? .repo : .token
        case .words: step = .name
        case .choose: step = .words
        default: break
        }
    }

    private func next() {
        problem = nil
        switch step {
        case .repo where signingIn:
            if loggingIn {
                problem = L10n.t("Notion 연결을 기다리는 중이에요", "Still waiting for Notion")
            } else if login == nil {
                problem = L10n.t("먼저 'Notion으로 연결'을 눌러 주세요", "Click 'Connect with Notion' first")
            } else if loginPage.isEmpty {
                problem = L10n.t("동기화에 쓸 페이지를 골라 주세요", "Pick the page to sync with")
            } else {
                page = loginPage
                step = .name
            }
        case .repo:
            switch backend == .git ? Wizard.checkRepoURL(repoText) : Wizard.checkNotionPage(pageText) {
            case .ok(let normal):
                if backend == .git {
                    repo = normal
                    repoText = normal
                    kind = StorageKind.forAddress(normal, chosen: kind)  // the token help follows the address
                } else {
                    page = normal
                }
                step = .token
            case .invalid(let why):
                problem = why
            }
        case .token:
            if Wizard.cleanToken(tokenText).isEmpty {
                problem = backend == .git ? L10n.t("토큰을 붙여 넣어 주세요", "Paste the token") : L10n.t("비밀 키를 붙여 넣어 주세요", "Paste the secret key")
            } else {
                step = .name
            }
        case .name:
            if Wizard.isValidPCName(pcName) { step = .words } else { problem = L10n.t("영문·숫자·하이픈만 쓸 수 있어요", "Letters, digits and hyphens only") }
        case .words:
            if path == .first { step = .choose } else { run() }
        case .choose:
            run()
        default:
            break
        }
    }

    private func run() {
        guard let client = model.client else {
            problem = L10n.t("앱 안의 동기화 엔진을 찾지 못했어요. 앱을 다시 설치해 주세요.", "The sync engine inside the app is missing. Please reinstall the app.")
            return
        }
        let token = Wizard.cleanToken(tokenText)
        tokenText = ""
        let signedIn = signingIn
        guard signedIn || !token.isEmpty else {
            step = .token
            problem = backend == .git ? L10n.t("토큰을 다시 붙여 넣어 주세요", "Paste the token again") : L10n.t("비밀 키를 다시 붙여 넣어 주세요", "Paste the secret key again")
            return
        }
        step = .running
        let words = Wizard.words(wordsText)
        var choice = InitChoice(remote: repo, username: pcName, pc: pcName, skills: skills.sorted(), mods: mods.sorted(),
                                keys: keys.sorted(), moveClaudeMd: moveClaudeMd, requireApproval: requireApproval,
                                backend: backend, notionPage: page)
        choice.words = words
        choice.notionSignedIn = signedIn
        let isFirst = path == .first
        let pc = pcName
        let remote = repo
        let notionPage = page
        let viaNotion = backend == .notion
        let onlyReceive = receiveOnly
        Task {
            let outcome: Result<JoinResult, EngineFailure>? = await model.exclusive(L10n.t("설정하는 중", "Setting up")) {
                if isFirst {
                    return await client.initFirstPC(choice, token: token)
                } else if viaNotion {
                    return await client.joinNotion(page: notionPage, pc: pc, words: words, receiveOnly: onlyReceive,
                                                   secret: signedIn ? nil : token)
                } else {
                    return await client.join(remote: remote, username: pc, pc: pc, words: words,
                                             receiveOnly: onlyReceive, token: token)
                }
            }
            switch outcome {
            case .success(let r):
                result = r
            case .failure(let f):
                // The secret was cleared when it was handed over: ask for it again (or sign in again).
                problem = f.message
                step = signedIn ? .repo : .token
                return
            case nil:
                problem = signedIn ? L10n.t("다른 작업이 끝난 뒤 다시 시도해 주세요", "Try again when the other task is done")
                    : L10n.t("다른 작업이 끝난 뒤 다시 시도해 주세요. 비밀 키(토큰)도 다시 붙여 넣어 주세요",
                             "Try again when the other task is done, and paste the secret key (token) again")
                step = signedIn ? .repo : .token
                return
            }
            step = .result
            await model.refresh()
        }
    }
}
