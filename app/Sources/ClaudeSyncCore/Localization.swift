import Foundation
import NaturalLanguage

/// The app's language, chosen in the settings (first: the Mac's own).
public enum AppLanguage: String, CaseIterable, Identifiable, Sendable {
    case ko, en

    public var id: Self { self }

    /// Each name in its own language, so either reader can find it.
    public var displayName: String { self == .ko ? "한국어" : "English" }

    public static func fromSystem(_ preferred: [String] = Locale.preferredLanguages) -> AppLanguage {
        (preferred.first ?? "").hasPrefix("ko") ? .ko : .en
    }
}

/// Every text the app makes, in both languages side by side: `L10n.t("한국어", "English")`.
/// (Texts from the engine are its own, in Korean.)
public enum L10n {
    private static let lock = NSLock()
    nonisolated(unsafe) private static var stored: AppLanguage = .ko
    /// Tests pick a language for their own task only.
    @TaskLocal public static var override: AppLanguage?

    public static var current: AppLanguage {
        get {
            if let override { return override }
            lock.lock()
            defer { lock.unlock() }
            return stored
        }
        set {
            lock.lock()
            stored = newValue
            lock.unlock()
        }
    }

    public static func t(_ ko: String, _ en: String) -> String {
        current == .en ? en : ko
    }
}

/// Which skill and mod descriptions (written in their own files, often in English) need translating.
public enum DescriptionLanguage {
    /// The language a text is written in, as a language code ("en", "ja", "zh-Hans"…); nil when unsure.
    public static func source(of text: String) -> String? {
        let recognizer = NLLanguageRecognizer()
        recognizer.processString(text)
        return recognizer.dominantLanguage?.rawValue
    }

    /// Written in a language other than the app's (any language macOS can tell).
    public static func needsTranslation(_ text: String, to target: AppLanguage) -> Bool {
        guard let source = source(of: text) else { return false }
        return source.split(separator: "-").first.map(String.init) != target.rawValue
    }
}

/// Translated descriptions, kept per language (and saved, so each is translated once).
public struct TranslationCache: Equatable, Sendable {
    public private(set) var dictionary: [String: String]

    public init(dictionary: [String: String] = [:]) {
        self.dictionary = dictionary
    }

    private static func key(_ original: String, _ language: AppLanguage) -> String {
        language.rawValue + "\u{1F}" + original
    }

    public func has(_ original: String, in language: AppLanguage) -> Bool {
        dictionary[Self.key(original, language)] != nil
    }

    /// The translation when there is one, else the text as written.
    public func text(for original: String, in language: AppLanguage) -> String {
        dictionary[Self.key(original, language)] ?? original
    }

    public mutating func store(_ original: String, _ translated: String, in language: AppLanguage) {
        dictionary[Self.key(original, language)] = translated
    }
}
