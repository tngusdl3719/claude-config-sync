import AppKit
import ClaudeSyncCore
import SwiftUI

/// Picks the folder a folder storage lives in: the "Claude Sync" folder inside one of the sync apps' folders
/// this Mac has, or any folder chosen by hand.
struct FolderChoiceView: View {
    @Binding var folder: String
    var intro: String
    private let found = CloudFolders.find(home: NSHomeDirectory())

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(intro).font(.callout)
            if found.isEmpty {
                Text(L10n.t("이 Mac에서 iCloud Drive·Dropbox·Google Drive·OneDrive 폴더를 찾지 못했어요. 아래 버튼으로 직접 골라 주세요.",
                            "No iCloud Drive, Dropbox, Google Drive or OneDrive folder was found on this Mac. Choose one with the button below."))
                    .font(.callout)
                    .foregroundStyle(.orange)
            } else {
                Picker(L10n.t("폴더", "Folder"), selection: $folder) {
                    ForEach(found) { Text($0.name).tag($0.store) }
                    if !folder.isEmpty && !found.contains(where: { $0.store == folder }) {
                        Text(L10n.t("직접 고른 폴더", "The folder chosen")).tag(folder)
                    }
                }
                .pickerStyle(.radioGroup)
                .labelsHidden()
            }
            Button(L10n.t("다른 폴더 고르기…", "Choose another folder…")) { choose() }
            if !folder.isEmpty {
                Text(CloudFolders.describe(folder, home: NSHomeDirectory()))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
            Text(L10n.t("다른 PC에서도 같은 계정의 같은 폴더를 고르세요. 그 동기화 앱이 켜져 있어야 서로 주고받아요.",
                        "Other PCs choose the same folder of the same account. Its sync app must be running for the PCs to exchange."))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .onAppear { if folder.isEmpty, let first = found.first { folder = first.store } }
    }

    private func choose() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = L10n.t("고르기", "Choose")
        panel.message = L10n.t("공용 설정을 둘 폴더를 고르세요. 동기화 앱이 다른 PC로 옮겨 주는 곳이어야 해요.",
                               "Choose the folder for the shared settings. A sync app must carry it to the other PCs.")
        if panel.runModal() == .OK, let url = panel.url { folder = url.path }
    }
}
