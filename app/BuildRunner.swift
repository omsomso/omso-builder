import AppKit
import CryptoKit
import Darwin
import Foundation

/// A finished build's installation as prepared by the builder core: the install
/// script it validated, and fingerprints binding it to the tested files.
struct Installation: Decodable {
    struct Refusal: Decodable { let path: String; let message: String }
    let source: String
    let lock_path: String
    let summary: [String]
    let fingerprints: [String: String]
    let post_install: [[String]]
    let refuse_if_exists: [Refusal]
    let notes: [String]
    let script: String

    func validate(source: BuildSource) throws {
        guard self.source == source.path, lock_path.hasPrefix(source.path + "/"), !summary.isEmpty,
              !script.isEmpty, !fingerprints.isEmpty, fingerprints.keys.allSatisfy({ $0.hasPrefix(source.path + "/") }),
              post_install.allSatisfy({ !$0.isEmpty && $0[0].hasPrefix("/") }) else {
            throw Builder.error("Invalid installation result. Build again.")
        }
        for refusal in refuse_if_exists where FileManager.default.fileExists(atPath: refusal.path) {
            throw Builder.error(refusal.message)
        }
        for (path, expected) in fingerprints {
            let file = try FileHandle(forReadingFrom: URL(fileURLWithPath: path))
            defer { try? file.close() }
            var digest = SHA256()
            while let data = try file.read(upToCount: 1024 * 1024), !data.isEmpty { digest.update(data: data) }
            let actual = digest.finalize().map { String(format: "%02x", $0) }.joined()
            guard actual == expected else { throw Builder.error("The built files changed. Build again before installing.") }
        }
    }
}

private struct BuilderResponse: Decodable {
    struct Failure: Decodable { let message: String }
    let ok: Bool
    let status: String?
    let error: Failure?
    let installation: Installation?
}

final class BuildRunner: ObservableObject {
    static var isBusy = false
    @Published var running = false { didSet { Self.isBusy = running } }
    @Published var log = "Build progress will appear here."
    @Published var message = "Close apps that use the products before installing."
    @Published var failure = false
    @Published var canInstall = false
    @Published var logURL: URL?
    private var pending: (Installation, BuildSource)?
    // Reuse one unchanged script in this process. macOS controls how long its
    // authorization is cached; no password or permanent authorization is stored.
    private let administratorScript = NSAppleScript(source: """
        on run argv
            do shell script (item 1 of argv) with administrator privileges without altering line endings
        end run
        """)!

    private static func runEvent(_ command: String) -> NSAppleEventDescriptor {
        let event = NSAppleEventDescriptor(eventClass: AEEventClass(kCoreEventClass), eventID: AEEventID(kAEOpenApplication),
                                          targetDescriptor: nil, returnID: AEReturnID(kAutoGenerateReturnID), transactionID: AETransactionID(kAnyTransactionID))
        let arguments = NSAppleEventDescriptor.list()
        arguments.insert(NSAppleEventDescriptor(string: command), at: 1)
        event.setParam(arguments, forKeyword: AEKeyword(keyDirectObject))
        return event
    }

    static func selfTest() throws {
        let runner = BuildRunner()
        var scriptError: NSDictionary?
        precondition(runner.administratorScript.compileAndReturnError(&scriptError), "Invalid authorization script")
        // Exercise the same Apple-event argument transport without elevation.
        let echo = NSAppleScript(source: "on run argv\nreturn item 1 of argv\nend run")!
        let payload = "literal 'quoted' \"text\" and $variables\nsecond line"
        let echoed = echo.executeAppleEvent(runEvent(payload), error: &scriptError)
        precondition(scriptError == nil && echoed.stringValue == payload)
        var output = ""
        let exitCode = try process(["/bin/echo", "fixture progress"], report: { output += $0 })
        precondition(exitCode == 0)
        precondition(output.contains("fixture progress"))
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let file = root.appendingPathComponent("build/Demo.app/Contents/Info.plist")
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        let data = Data("fixture".utf8)
        try data.write(to: file)
        let hash = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        let source = BuildSource(path: root.path, branch: "fixture", head: "fixture", dirty: false, primary: true, products: ["Demo"])
        func installation(source path: String? = nil, lock: String? = nil, fingerprints: [String: String]? = nil,
                          post: [[String]] = [], refuse: [Installation.Refusal] = []) -> Installation {
            Installation(source: path ?? root.path, lock_path: lock ?? root.appendingPathComponent("build/.omso-builder.lock").path,
                         summary: ["Copy Demo.app to /Applications/omso"], fingerprints: fingerprints ?? [file.path: hash],
                         post_install: post, refuse_if_exists: refuse, notes: [], script: "set -e\n")
        }
        try installation().validate(source: source)
        try installation(post: [[root.appendingPathComponent("register.sh").path]]).validate(source: source)
        try Data("changed build".utf8).write(to: file)
        do { try installation().validate(source: source); preconditionFailure("Stale output accepted") } catch {}
        try data.write(to: file)
        let invalid = [installation(source: "/tmp/elsewhere"), installation(lock: "/tmp/x.lock"),
                       installation(fingerprints: [:]), installation(fingerprints: ["/etc/hosts": hash]),
                       installation(post: [["relative.sh"]]),
                       installation(refuse: [Installation.Refusal(path: file.path, message: "Resolve it first.")])]
        for item in invalid {
            do { try item.validate(source: source); preconditionFailure("Invalid installation accepted") } catch {}
        }
    }

    private func append(_ text: String) {
        log += text
        if log.count > 120_000 { log = String(log.suffix(100_000)) }
    }

    func note(_ text: String) {
        append(text)
        if let url = logURL, let file = try? FileHandle(forWritingTo: url) {
            defer { try? file.close() }
            _ = try? file.seekToEnd()
            try? file.write(contentsOf: Data(text.utf8))
        }
    }

    static func process(_ command: [String], input: Data? = nil, output: URL? = nil,
                        report: @escaping (String) -> Void) throws -> Int32 {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: command[0])
        process.arguments = Array(command.dropFirst())
        process.environment = Builder.environment
        let logs = Pipe()
        process.standardError = logs
        let outputFile: FileHandle?
        if let output {
            FileManager.default.createFile(atPath: output.path, contents: nil)
            outputFile = try FileHandle(forWritingTo: output)
            process.standardOutput = outputFile
        } else {
            outputFile = nil
            process.standardOutput = logs
        }
        defer { try? outputFile?.close() }
        let stdin = Pipe()
        process.standardInput = stdin
        try process.run()
        if let input { try stdin.fileHandleForWriting.write(contentsOf: input) }
        try stdin.fileHandleForWriting.close()
        var pending = Data()
        while let chunk = try logs.fileHandleForReading.read(upToCount: 8192), !chunk.isEmpty {
            pending.append(chunk)
            if let text = String(data: pending, encoding: .utf8) { report(text); pending.removeAll(keepingCapacity: true) }
            else if pending.count > 65536 { report(String(decoding: pending, as: UTF8.self)); pending.removeAll() }
        }
        if !pending.isEmpty { report(String(decoding: pending, as: UTF8.self)) }
        process.waitUntilExit()
        return process.terminationStatus
    }

    private func newLog(builder: Builder) throws -> URL {
        let folder = builder.root.appendingPathComponent("build/runs")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let url = folder.appendingPathComponent(UUID().uuidString).appendingPathExtension("log")
        FileManager.default.createFile(atPath: url.path, contents: nil)
        logURL = url
        return url
    }

    func start(builder: Builder, source: BuildSource, selected: Set<String>, options: [String: Bool], preview: Bool) {
        guard !running else { return }
        do {
            let input = try builder.request(source: source, selected: selected, options: options, preview: preview)
            let logURL = try newLog(builder: builder)
            let responseURL = logURL.deletingPathExtension().appendingPathExtension("json")
            log = ""; failure = false; running = true; canInstall = false; pending = nil
            message = preview ? "Previewing…" : "Building and testing…"
            let started = Date()
            DispatchQueue.global(qos: .userInitiated).async {
                do {
                    let file = try FileHandle(forWritingTo: logURL)
                    defer { try? file.close() }
                    let code = try Self.process([Builder.python, builder.api.path], input: input, output: responseURL) { text in
                        try? file.write(contentsOf: Data(text.utf8))
                        DispatchQueue.main.async { self.append(text) }
                    }
                    let response = try JSONDecoder().decode(BuilderResponse.self, from: Data(contentsOf: responseURL))
                    DispatchQueue.main.async {
                        self.note("\nWorkflow elapsed: \(Int(Date().timeIntervalSince(started))) seconds.\n")
                        guard code == 0 && response.ok else {
                            self.running = false; self.failure = true
                            self.message = response.error?.message ?? "Build did not complete. See the log."
                            self.note(self.message + "\n")
                            return
                        }
                        if preview && response.status == "previewed" {
                            self.running = false; self.message = "Preview complete. Nothing was built or installed."
                        } else if response.status == "awaiting_install", let installation = response.installation {
                            self.pending = (installation, source)
                            self.running = false; self.canInstall = true
                            self.install()
                        } else {
                            self.running = false; self.failure = true
                            self.message = "Unexpected builder result; installation was not started."
                        }
                    }
                } catch {
                    DispatchQueue.main.async {
                        self.running = false; self.failure = true; self.message = error.localizedDescription
                        self.note("\n" + error.localizedDescription + "\n")
                    }
                }
            }
        } catch { failure = true; message = error.localizedDescription }
    }

    /// Installs a build that already passed its tests, without rebuilding it.
    func installReady(builder: Builder, build: ReadyBuild) {
        guard !running else { return }
        do { _ = try newLog(builder: builder) } catch { failure = true; message = error.localizedDescription; return }
        log = ""; failure = false; running = true; canInstall = false; pending = nil
        message = "Checking the finished build…"
        note("Installing ready build: \(build.repository.name) · \(build.name) · \(build.configuration) · "
             + build.products.joined(separator: ", ") + "\nBuild \(build.build_id)\nFolder: \(build.source.path)\n")
        DispatchQueue.global(qos: .userInitiated).async {
            let result = Result { try JSONDecoder().decode(BuilderResponse.self, from: builder.call([
                "api_version": 2, "operation": "install", "source_dir": build.source.path,
                "build": build.key, "build_id": build.build_id])) }
            DispatchQueue.main.async {
                self.running = false
                switch result {
                case .success(let response):
                    guard response.ok, response.status == "awaiting_install", let installation = response.installation else {
                        self.failure = true
                        self.message = response.error?.message ?? "This build cannot be installed. Refresh the list."
                        self.note(self.message + "\n")
                        return
                    }
                    self.pending = (installation, build.source); self.canInstall = true
                    self.install()
                case .failure(let error):
                    self.failure = true; self.message = error.localizedDescription
                    self.note(error.localizedDescription + "\n")
                }
            }
        }
    }

    func install() {
        guard !running, let (installation, source) = pending else { return }
        running = true; failure = false; message = "Checking built files before installation…"
        DispatchQueue.global(qos: .userInitiated).async {
            let descriptor = Darwin.open(installation.lock_path, O_RDWR | O_CREAT, 0o600)
            guard descriptor >= 0 else {
                DispatchQueue.main.async { self.running = false; self.failure = true; self.message = "Could not lock this build." }
                return
            }
            do {
                guard flock(descriptor, LOCK_EX | LOCK_NB) == 0 else { throw Builder.error("Another build is running. Retry installation when it finishes.") }
                try installation.validate(source: source)
                DispatchQueue.main.async {
                    self.message = "Installing… Approve the macOS dialog if shown."
                    self.note("\nInstalling:\n" + installation.summary.map { "  " + $0 }.joined(separator: "\n") + "\n")
                    var error: NSDictionary?
                    let result = self.administratorScript.executeAppleEvent(Self.runEvent(installation.script), error: &error)
                    if let error {
                        flock(descriptor, LOCK_UN); Darwin.close(descriptor)
                        self.running = false; self.failure = true
                        self.message = "Installation was not completed. You can retry without rebuilding."
                        self.note("\nInstallation: \(error)\n")
                        return
                    }
                    self.note((result.stringValue ?? "") + "\n")
                    DispatchQueue.global(qos: .userInitiated).async {
                        defer { flock(descriptor, LOCK_UN); Darwin.close(descriptor) }
                        do {
                            for command in installation.post_install {
                                let code = try Self.process(command) { text in DispatchQueue.main.async { self.note(text) } }
                                guard code == 0 else { throw Builder.error("Files installed, but a follow-up step failed. See the log and retry.") }
                            }
                            DispatchQueue.main.async {
                                self.running = false; self.failure = false; self.canInstall = false; self.pending = nil
                                self.message = (["Installed."] + installation.notes).joined(separator: " ")
                                self.note((["Installation completed."] + installation.notes).joined(separator: "\n") + "\n")
                            }
                        } catch {
                            DispatchQueue.main.async { self.running = false; self.failure = true; self.message = error.localizedDescription }
                        }
                    }
                }
            } catch {
                flock(descriptor, LOCK_UN); Darwin.close(descriptor)
                DispatchQueue.main.async { self.running = false; self.failure = true; self.message = error.localizedDescription }
            }
        }
    }
}
