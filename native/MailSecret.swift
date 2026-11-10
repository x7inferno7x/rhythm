import Foundation
import Security
let args = CommandLine.arguments
 guard args.count == 3, ["gmail"].contains(args[1]), ["get", "set"].contains(args[2]) else { exit(2) }
let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "ru.personal-ai-planner.mail." + args[1], kSecAttrAccount as String: "imap"]
if args[2] == "set" {
 let data = FileHandle.standardInput.readDataToEndOfFile()
 guard !data.isEmpty && data.count < 4096 else { exit(2) }
 let status = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
 if status == errSecItemNotFound {
  var item = query; item[kSecValueData as String] = data; item[kSecAttrLabel as String] = "Ритм — " + args[1]
  exit(SecItemAdd(item as CFDictionary, nil) == errSecSuccess ? 0 : 3)
 }
 exit(status == errSecSuccess ? 0 : 3)
} else {
 var item = query; item[kSecReturnData as String] = true; item[kSecMatchLimit as String] = kSecMatchLimitOne
 var result: CFTypeRef?
 guard SecItemCopyMatching(item as CFDictionary, &result) == errSecSuccess, let data = result as? Data else { exit(4) }
 FileHandle.standardOutput.write(data)
}
