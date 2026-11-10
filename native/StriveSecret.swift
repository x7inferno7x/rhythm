import Foundation
import Security
let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "ru.personal-ai-planner.strive", kSecAttrAccount as String: "api-token"]
let mode = CommandLine.arguments.dropFirst().first ?? "get"
if mode == "set" {
    let data = FileHandle.standardInput.readDataToEndOfFile()
    guard !data.isEmpty && data.count < 1024 else { exit(2) }
    let status = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
    if status == errSecItemNotFound {
        var item = query
        item[kSecValueData as String] = data
        item[kSecAttrLabel as String] = "Ритм — Strive API"
        let added = SecItemAdd(item as CFDictionary, nil)
        exit(added == errSecSuccess ? 0 : 3)
    }
    exit(status == errSecSuccess ? 0 : 3)
} else {
    var item = query
    item[kSecReturnData as String] = true
    item[kSecMatchLimit as String] = kSecMatchLimitOne
    var result: CFTypeRef?
    let status = SecItemCopyMatching(item as CFDictionary, &result)
    guard status == errSecSuccess, let data = result as? Data else { exit(4) }
    if mode == "get" { FileHandle.standardOutput.write(data) }
}
