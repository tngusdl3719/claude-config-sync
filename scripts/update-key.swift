// The update signing key's arithmetic (Ed25519, CryptoKit); scripts/update-key.sh keeps the key in the keychain.
//   new          → two lines: the private key, then the public key (base64)
//   public       ← the private key on stdin → the public key
//   sign <file>  ← the private key on stdin → the file's signature (base64)
import CryptoKit
import Foundation

func fail(_ text: String) -> Never {
    FileHandle.standardError.write(Data(("✗ " + text + "\n").utf8))
    exit(1)
}

func privateKeyFromStdin() -> Curve25519.Signing.PrivateKey {
    let text = String(decoding: FileHandle.standardInput.readDataToEndOfFile(), as: UTF8.self)
        .trimmingCharacters(in: .whitespacesAndNewlines)
    guard let raw = Data(base64Encoded: text), let key = try? Curve25519.Signing.PrivateKey(rawRepresentation: raw) else {
        fail("not a private key")
    }
    return key
}

let args = CommandLine.arguments.dropFirst()
switch args.first {
case "new":
    let key = Curve25519.Signing.PrivateKey()
    print(key.rawRepresentation.base64EncodedString())
    print(key.publicKey.rawRepresentation.base64EncodedString())
case "public":
    print(privateKeyFromStdin().publicKey.rawRepresentation.base64EncodedString())
case "sign":
    guard args.count == 2, let data = FileManager.default.contents(atPath: args[args.startIndex + 1]) else {
        fail("usage: update-key.swift sign <file>")
    }
    guard let signature = try? privateKeyFromStdin().signature(for: data) else { fail("could not sign") }
    print(signature.base64EncodedString())
default:
    fail("usage: update-key.swift new | public | sign <file>")
}
