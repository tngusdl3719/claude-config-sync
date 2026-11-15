import AppKit
import ClaudeSyncCore
import SwiftUI

extension Tint {
    var color: Color {
        switch self {
        case .normal: return .secondary
        case .attention: return .orange
        case .alert: return .red
        }
    }
}

/// The diff and history windows, opened from the menu panel and the main window.
@MainActor
enum Viewers {
    static func diff(_ model: AppModel, _ item: String, title: @escaping @autoclosure () -> String) {
        Task {
            guard let client = model.client else { return }
            switch await client.diff(item) {
            case .success(let reply):
                Windows.show("diff", title: title()) { DiffView(text: reply.text).environment(model) }
            case .failure(let f):
                model.message = f.message
            }
        }
    }
}

/// Symbol, headline, mode, last receive/send, the other PCs, and the last error.
struct StatusHeader: View {
    @Environment(AppModel.self) private var model
    var large = false

    var body: some View {
        let bar = model.menuBar
        HStack(alignment: .top, spacing: large ? 14 : 10) {
            Image(systemName: bar.symbol)
                .font(large ? .largeTitle : .title2)
                .foregroundStyle(bar.tint.color)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(bar.headline).font(large ? .title2.bold() : .headline)
                    if let mode = bar.modeLabel {
                        Text(mode)
                            .font(.caption.bold())
                            .padding(.horizontal, 6)
                            .padding(.vertical, 1)
                            .background(Capsule().fill(Color.blue.opacity(0.18)))
                    }
                }
                if let s = model.status {
                    let now = s.now ?? Date().timeIntervalSince1970
                    Text({
                        let pull = s.lastPull.map { Presentation.clock($0, now: now) } ?? "-"
                        let push = s.lastPush.map { Presentation.clock($0, now: now) } ?? "-"
                        let pc = s.pc ?? "-"
                        let storage = Presentation.storage(s.storage).title
                        return L10n.t("받기 \(pull) · 올리기 \(push) · 이 PC \(pc) · \(storage)",
                                      "Received \(pull) · Sent \(push) · This PC \(pc) · \(storage)")
                    }())
                        .font(large ? .callout : .caption)
                        .foregroundStyle(.secondary)
                    ForEach(s.others, id: \.pc) { other in
                        Text(L10n.t("\(other.pc) 마지막 동기화: ", "\(other.pc) last synced: ") + Presentation.clock(other.time, now: now))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    if let error = s.lastError, s.lastResult != .ok {
                        Text(error).font(.caption).foregroundStyle(.red).lineLimit(3).textSelection(.enabled)
                    }
                }
            }
        }
    }
}

/// '지금 동기화' and the automatic-sync switch.
struct SyncControls: View {
    @Environment(AppModel.self) private var model
    let status: Status
    var large = false

    var body: some View {
        HStack(spacing: 10) {
            Button {
                Task { await model.syncNow(manual: true) }
            } label: {
                Label(model.syncing ? L10n.t("동기화 중…", "Syncing…") : L10n.t("지금 동기화", "Sync now"), systemImage: "arrow.triangle.2.circlepath")
                    .frame(minWidth: large ? 130 : nil)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(large ? .large : .small)
            .disabled(model.syncing || model.busy != nil)
            Toggle(large ? L10n.t("자동 동기화 (\(model.intervalMinutes)분마다)", "Auto sync (every \(model.intervalMinutes) min)")
                   : L10n.t("자동", "Auto"), isOn: Binding(
                get: { status.autoEnabled },
                set: { on in model.perform(L10n.t("바꾸는 중", "Changing")) { await $0.setAuto(on) } }))
                .toggleStyle(.switch)
                .controlSize(.small)
                .fixedSize()
        }
    }
}

/// What needs a decision: blocked words, conflicts, approvals, skipped names.
struct ProblemsList: View {
    @Environment(AppModel.self) private var model
    let s: Status

    var body: some View {
        if !s.blocked.isEmpty {
            ProblemBox(title: L10n.t("올리기 멈춤 — 금지어 발견", "Sending stopped — blocked word found"), color: .red) {
                ForEach(Array(s.blocked.prefix(5).enumerated()), id: \.offset) { _, hit in
                    Text("\(hit.file)\(hit.line > 0 ? L10n.t(" \(hit.line)행", " line \(hit.line)") : "")  \"\(String(hit.text.prefix(60)))\"  · \(hit.word)")
                        .font(.caption)
                }
                HStack {
                    Button(L10n.t("파일 열기", "Open file")) {
                        if let hit = s.blocked.first {
                            NSWorkspace.shared.open(URL(fileURLWithPath: model.home + "/.claude/shared/" + hit.file))
                        }
                    }
                    Button(L10n.t("이번 변경 되돌리기", "Undo this change")) {
                        model.perform(L10n.t("되돌리는 중", "Undoing"), done: L10n.t("올리지 않은 변경을 취소했어요", "The unsent change was undone")) { await $0.discardBlocked() }
                    }
                    if Presentation.canSend(s) {
                        Button(L10n.t("이번만 허용", "Allow once")) {
                            model.perform(L10n.t("올리는 중", "Sending"), done: L10n.t("이번 변경을 올렸어요", "This change was sent")) { await $0.allowOnce() }
                        }
                    }
                }
            }
        }
        ForEach(s.conflicts) { c in
            ProblemBox(title: L10n.t("충돌 · ", "Conflict · ") + (c.kind == "file" ? (c.file ?? "")
                       : L10n.t("설정 ", "setting ") + (c.path ?? []).joined(separator: ".")), color: .red) {
                if c.kind == "setting" {
                    Text({
                        let none = L10n.t("(없음)", "(none)")
                        return L10n.t("이 PC: \(c.local?.display ?? none)   서버: \(c.remote?.display ?? none)",
                                      "This PC: \(c.local?.display ?? none)   Server: \(c.remote?.display ?? none)")
                    }()).font(.caption)
                }
                HStack {
                    Button(L10n.t("이 PC 값 유지", "Keep this PC's")) {
                        model.perform(L10n.t("정리하는 중", "Resolving"), done: L10n.t("이 PC 값을 유지했어요", "Kept this PC's value")) { await $0.resolve(c.id, take: false) }
                    }
                    Button(L10n.t("서버 값 받기", "Take the server's")) {
                        model.perform(L10n.t("정리하는 중", "Resolving"), done: L10n.t("서버 값을 받았어요", "Took the server's value")) { await $0.resolve(c.id, take: true) }
                    }
                }
            }
        }
        if let approval = s.pendingApproval.first {
            ProblemBox(title: {
                let who = approval.commits.first?.pc ?? L10n.t("다른 PC", "another PC")
                return L10n.t("확인 필요 · \(who)에서 실행 파일이 바뀌었어요", "Check needed · executables changed on \(who)")
            }(), color: .orange) {
                ForEach(approval.files.prefix(6), id: \.self) { Text($0).font(.caption.monospaced()) }
                ForEach(approval.settings, id: \.self) { Text(L10n.t("설정 ", "setting ") + $0.joined(separator: ".")).font(.caption) }
                HStack {
                    Button(L10n.t("바뀐 내용 보기", "Show changes")) { Viewers.diff(model, "approval", title: L10n.t("확인할 변경", "Changes to check")) }
                    Button(L10n.t("승인", "Approve")) {
                        model.perform(L10n.t("적용하는 중", "Applying"), done: L10n.t("승인하고 적용했어요", "Approved and applied")) { await $0.approve() }
                    }
                        .buttonStyle(.borderedProminent)
                    Button(L10n.t("거절", "Reject")) {
                        model.perform(L10n.t("거절하는 중", "Rejecting"), done: L10n.t("거절하고 되돌렸어요", "Rejected and rolled back")) { await $0.reject() }
                    }
                }
            }
        }
        if !s.collisions.isEmpty {
            Text(L10n.t("이름이 겹쳐 건너뛴 공용 항목: ", "Shared items skipped for a name clash: ")
                 + s.collisions.map { "\($0.kind)/\($0.name)" }.joined(separator: ", "))
                .font(.caption)
                .foregroundStyle(.orange)
        }
    }

    static func any(_ s: Status) -> Bool {
        !s.blocked.isEmpty || !s.conflicts.isEmpty || !s.pendingApproval.isEmpty || !s.collisions.isEmpty
    }
}

struct ProblemBox<Content: View>: View {
    let title: String
    let color: Color
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.subheadline.bold()).foregroundStyle(color)
            content
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 6).fill(color.opacity(0.08)))
    }
}

/// The shared items (synced) and the PC-only ones, with their states and actions.
struct ItemsList: View {
    @Environment(AppModel.self) private var model
    let s: Status

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(L10n.t("공용 (동기화됨)", "Shared (synced)")).font(.headline)
            if let items = s.items {
                row(L10n.t("지침", "Instructions"), help: "claude_md", detail: "shared/CLAUDE.md", state: items.claudeMd?.state ?? .ok) {
                    Viewers.diff(model, "claude_md", title: L10n.t("지침 바뀐 내용", "Instruction changes"))
                }
                if let settings = items.settings {
                    DisclosureGroup {
                        VStack(alignment: .leading, spacing: 3) {
                            ForEach(settings.keys, id: \.self) { key in
                                HStack(alignment: .firstTextBaseline, spacing: 10) {
                                    Text(key).font(.caption.monospaced())
                                    Text(Presentation.settingHelp(key)).font(.caption).foregroundStyle(.secondary)
                                }
                                .help(Presentation.settingHelp(key))
                            }
                        }
                        .textSelection(.enabled)
                    } label: {
                        row(L10n.t("설정", "Settings"), help: "settings", detail: L10n.t("\(settings.count)개 항목", "\(settings.count) items"), state: settings.state) {
                            Viewers.diff(model, "settings", title: L10n.t("설정 바뀐 내용", "Settings changes"))
                        }
                    }
                }
                group(L10n.t("스킬", "Skills"), kind: "skills", items: items.skills, canSend: Presentation.canSend(s))
                group("mods", kind: "mods", items: items.mods, canSend: Presentation.canSend(s))
            }
            Divider()
            HStack(spacing: 4) {
                Text(L10n.t("이 PC 전용 (동기화 안 됨)", "This PC only (not synced)")).font(.headline)
                InfoButton(text: Presentation.groupHelp("local"))
            }
            if let local = s.localOnly {
                ForEach(["skills", "mods"], id: \.self) { kind in
                    ForEach(kind == "skills" ? local.skills : local.mods, id: \.self) { name in
                        HStack {
                            described("\(kind == "skills" ? L10n.t("스킬", "skill") : "mod") \(name)", s.descriptions.of(kind, name, local: true).map(model.describe))
                            Spacer()
                            if Presentation.canSend(s) {
                                Button(L10n.t("공용으로 보내기", "Share")) {
                                    model.performSimple(L10n.t("보내는 중", "Sharing"), done: L10n.t("\(name): 공용으로 보냈어요", "\(name): shared")) { await $0.share(kind: kind, name: name) }
                                }
                                .controlSize(.small)
                            }
                        }
                    }
                }
                Text(L10n.t("설정 ", "Settings: ") + (local.settings.isEmpty ? L10n.t("없음", "none") : local.settings.joined(separator: ", ")))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .help(local.settings.map { "\($0): \(Presentation.settingHelp($0))" }.joined(separator: "\n"))
            }
        }
    }

    private func group(_ title: String, kind: String, items: [ItemStatus], canSend: Bool) -> some View {
        DisclosureGroup {
            ForEach(items, id: \.name) { item in
                HStack {
                    described(item.name, s.descriptions.of(kind, item.name, local: false).map(model.describe))
                    Text(Presentation.label(item.state)).font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button(L10n.t("바뀐 내용", "Changes")) { Viewers.diff(model, "\(kind)/\(item.name)", title: L10n.t("\(item.name) 바뀐 내용", "\(item.name) changes")) }
                    if canSend {
                        Button(L10n.t("공용에서 빼기", "Unshare")) {
                            model.performSimple(L10n.t("빼는 중", "Unsharing"), done: L10n.t("\(item.name): 이 PC 전용으로 돌렸어요", "\(item.name): now this PC only")) { await $0.unshare(kind: kind, name: item.name) }
                        }
                    }
                }
                .controlSize(.small)
            }
        } label: {
            HStack {
                Text(title)
                InfoButton(text: Presentation.groupHelp(kind))
                Text(names(items)).foregroundStyle(.secondary).lineLimit(1)
                Spacer()
                Text(Presentation.label(Presentation.groupState(items))).font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    /// A name with an ⓘ when its own file says what it is.
    private func described(_ name: String, _ text: String?) -> some View {
        HStack(spacing: 4) {
            Text(name)
            if let text { InfoButton(text: text) }
        }
    }

    private func row(_ title: String, help: String, detail: String, state: ItemState,
                     open: @escaping () -> Void) -> some View {
        HStack {
            Text(title)
            InfoButton(text: Presentation.groupHelp(help))
            Text(detail).foregroundStyle(.secondary)
            Spacer()
            Text(Presentation.label(state)).font(.caption).foregroundStyle(.secondary)
            Button(L10n.t("바뀐 내용", "Changes"), action: open).controlSize(.small)
        }
    }

    private func names(_ items: [ItemStatus]) -> String {
        if items.isEmpty { return L10n.t("없음", "none") }
        let head = items.prefix(2).map(\.name).joined(separator: ", ")
        return items.count > 2 ? L10n.t("\(head) 외 \(items.count - 2)", "\(head) and \(items.count - 2) more") : head
    }
}

/// An ⓘ that shows its text in a bubble when clicked (and as a tooltip on hover).
struct InfoButton: View {
    let text: String
    @State private var shown = false

    var body: some View {
        Button { shown.toggle() } label: {
            Image(systemName: "info.circle").font(.caption)
        }
        .buttonStyle(.borderless)
        .foregroundStyle(.secondary)
        .help(text)
        .accessibilityLabel(L10n.t("설명 보기", "Show description"))
        .popover(isPresented: $shown, arrowEdge: .bottom) {
            Text(text)
                .font(.callout)
                .fixedSize(horizontal: false, vertical: true)
                .frame(width: 320, alignment: .leading)
                .padding(12)
                .textSelection(.enabled)
        }
    }
}

/// The engine change in progress, or the last message.
struct WorkLine: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        HStack(spacing: 6) {
            if let busy = model.busy {
                ProgressView().controlSize(.small)
                Text("\(busy)…").font(.caption)
            } else if let message = model.message {
                Text(message).font(.caption).foregroundStyle(.secondary).lineLimit(2)
            }
        }
    }
}
