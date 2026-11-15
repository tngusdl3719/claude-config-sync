import ClaudeSyncCore
import SwiftUI

/// 'Notion으로 연결': consent in the browser, then the page to sync from the ones the person shared.
struct NotionSignInView: View {
    @Environment(AppModel.self) private var model
    let oauthClient: NotionOAuthClient
    var intro: String
    @Binding var login: NotionLogin?
    @Binding var page: String
    @Binding var loggingIn: Bool
    @Binding var problem: String?
    var useSecretKey: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(intro).font(.callout)
            HStack {
                Button(login == nil ? L10n.t("Notion으로 연결", "Connect with Notion") : L10n.t("다시 연결", "Connect again")) { signIn() }
                    .buttonStyle(.borderedProminent)
                    .disabled(loggingIn)
                if loggingIn {
                    ProgressView().controlSize(.small)
                    Text(L10n.t("브라우저에서 Notion 연결을 기다리는 중… (5분)", "Waiting for Notion in the browser… (5 min)")).font(.caption)
                }
            }
            if let login {
                Text(L10n.t("연결됨: ", "Connected: ") + (login.workspace.isEmpty ? "Notion" : login.workspace)).font(.callout.bold())
                if login.pages.isEmpty {
                    Text(L10n.t("고른 페이지가 없어요. 다시 연결해서 페이지를 골라 주세요.", "No page was shared. Connect again and pick a page.")).font(.callout).foregroundStyle(.orange)
                } else {
                    Picker(L10n.t("동기화 페이지", "Sync page"), selection: $page) {
                        ForEach(login.pages) { p in
                            Text(p.title.isEmpty ? p.id : p.title).tag(p.id)
                        }
                    }
                }
            }
            Button(L10n.t("비밀 키로 연결하기", "Use a secret key instead"), action: useSecretKey).buttonStyle(.link)
        }
    }

    private func signIn() {
        guard let client = model.client else { return }
        problem = nil
        loggingIn = true
        Task {
            let result = await client.notionLogin(oauthClient)
            loggingIn = false
            switch result {
            case .success(let l):
                login = l
                // One page: that one. Several: keep the one already chosen (e.g. this PC's page) if offered.
                page = l.pages.count == 1 ? l.pages[0].id : (l.pages.first { $0.id == page }?.id ?? "")
            case .failure(let f):
                problem = f.message
            }
        }
    }
}
