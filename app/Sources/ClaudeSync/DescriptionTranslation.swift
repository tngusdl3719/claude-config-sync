import ClaudeSyncCore
import SwiftUI
import Translation

/// Skill and mod descriptions written in the other language (Korean ↔ English), translated on this
/// Mac by macOS (15 and later) and kept, so each is translated once. Elsewhere they show as written.
struct TranslatesDescriptions: ViewModifier {
    func body(content: Content) -> some View {
        if #available(macOS 15.0, *) {
            content.modifier(Translating())
        } else {
            content
        }
    }
}

@available(macOS 15.0, *)
private struct Translating: ViewModifier {
    @Environment(AppModel.self) private var model
    @State private var configuration: TranslationSession.Configuration?
    /// The language code the current session translates from.
    @State private var sourceCode: String?

    private struct Work: Equatable {
        let language: AppLanguage
        let texts: [String]
    }

    func body(content: Content) -> some View {
        content
            .task(id: Work(language: model.language, texts: model.untranslated)) {
                // One source language per session: the first text's; the rest follow as these are kept.
                guard let first = model.untranslated.first, let source = DescriptionLanguage.source(of: first) else { return }
                sourceCode = source
                let next = TranslationSession.Configuration(source: Locale.Language(identifier: source),
                                                            target: Locale.Language(identifier: model.language.rawValue))
                if configuration?.source == next.source && configuration?.target == next.target {
                    configuration?.invalidate()  // same languages, new texts: run the session again
                } else {
                    configuration = next
                }
            }
            .translationTask(configuration) { session in
                await translate(with: session)
            }
    }

    @MainActor
    private func translate(with session: TranslationSession) async {
        let target = model.language
        let texts = model.untranslated.filter { DescriptionLanguage.source(of: $0) == sourceCode }
        guard !texts.isEmpty else { return }
        let requests = texts.enumerated().map {
            TranslationSession.Request(sourceText: $0.element, clientIdentifier: String($0.offset))
        }
        do {
            let responses = try await session.translations(from: requests)
            let pairs = responses.compactMap { r -> (String, String)? in
                guard let i = r.clientIdentifier.flatMap(Int.init), texts.indices.contains(i) else { return nil }
                return (texts[i], r.targetText)
            }
            model.keepTranslations(pairs, in: target)
        } catch {
            // No language pack (declined or unavailable): these stay as written, and are not asked again.
            model.skipTranslations(texts, in: target)
        }
    }
}
