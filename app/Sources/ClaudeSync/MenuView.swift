import AppKit
import ClaudeSyncCore
import SwiftUI

/// The menu bar panel: a quick look (status, sync, what needs a decision) and the way to the main window.
struct MenuView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            StatusHeader()
            Divider()
            if !model.isConfigured {
                Text(L10n.t("아직 이 Mac에 동기화 설정이 없어요.", "Sync is not set up on this Mac yet."))
                Button(L10n.t("설정 시작하기…", "Set up…")) { WizardWindow.open(model) }
                    .buttonStyle(.borderedProminent)
            } else if let s = model.status {
                if let failure = model.failure {
                    Text(failure.message)
                        .font(.caption)
                        .foregroundStyle(.red)
                        .lineLimit(4)
                        .textSelection(.enabled)
                }
                SyncControls(status: s)
                if ProblemsList.any(s) {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 8) { ProblemsList(s: s) }
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .frame(maxHeight: 320)
                }
            } else {
                Text(model.failure?.message ?? L10n.t("상태를 읽는 중…", "Reading status…")).foregroundStyle(.secondary)
            }
            Divider()
            HStack {
                WorkLine()
                Spacer()
                Button(L10n.t("창 열기", "Open window")) { MainWindow.open(model) }
                Button(L10n.t("종료", "Quit")) { NSApplication.shared.terminate(nil) }
            }
            .controlSize(.small)
        }
        .padding(14)
        .frame(width: 380)
        .id(model.language)
    }
}
